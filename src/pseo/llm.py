"""LLM providers.

`GeminiProvider` is the real one. `MockProvider` is deterministic and offline: it lets the
whole pipeline (and its tests) run in CI without a key, and can be told to misbehave
(produce duplicate content) to prove the QA gates and the revise loop actually work.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Type, TypeVar

import numpy as np
from pydantic import BaseModel, ValidationError

log = logging.getLogger("pseo.llm")
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Raised when a call fails permanently (after retries)."""


class ModelOverloaded(LLMError):
    """The model keeps answering 503; try the next model in the chain instead of waiting."""


class QuotaExhausted(LLMError):
    """Daily quota for one model is used up; retrying today is pointless, try the next model."""


def _retry_delay(msg: str) -> float | None:
    """Server-suggested wait from a 429 body, e.g. "'retryDelay': '37s'"."""
    m = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", msg)
    return float(m.group(1)) if m else None


def _is_daily_quota(msg: str) -> bool:
    """True only when waiting a minute can't help. A 429 often lists several quota metrics
    (per-minute AND per-day ids), so the mere word "PerDay" is not enough: if the server
    suggests a short retry delay, it is a per-minute limit and we wait instead."""
    m = msg.lower()
    mentions_day = "perday" in m or "per_day" in m or "per day" in m or "limit: 0" in m
    delay = _retry_delay(msg)
    return mentions_day and (delay is None or delay > 120)


@dataclass
class Usage:
    calls: int = 0
    embed_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    retries: int = 0
    by_task: dict = field(default_factory=dict)

    def __post_init__(self):
        self._lock = threading.Lock()

    def as_dict(self) -> dict:
        return {k: v for k, v in vars(self).items() if not k.startswith("_")}

    def add(self, task: str, inp: int, out: int) -> None:
        with self._lock:
            self._add(task, inp, out)

    def _add(self, task: str, inp: int, out: int) -> None:
        self.calls += 1
        self.input_tokens += inp
        self.output_tokens += out
        self.by_task[task] = self.by_task.get(task, 0) + 1


class RateLimiter:
    """Simple sliding-window limiter so a scheduled run never trips the provider's RPM cap."""

    def __init__(self, rpm: int):
        self.interval = 60.0 / max(rpm, 1)
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            time.sleep(delay)


class Provider:
    name = "base"

    def __init__(self) -> None:
        self.usage = Usage()

    def generate(self, *, task: str, model: str, system: str, prompt: str,
                 schema: Type[T], temperature: float, context: dict | None = None,
                 thinking: str | None = None) -> T:
        raise NotImplementedError

    def embed(self, texts: list[str], *, model: str) -> np.ndarray:
        raise NotImplementedError


# ============================================================================ Gemini
class GeminiProvider(Provider):
    name = "gemini"

    RETRYABLE = {408, 429, 500, 502, 503, 504}

    def __init__(self, api_key: str | None = None, rpm: int = 8, max_retries: int = 6,
                 timeout_s: int = 180, embed_rpm: int = 60):
        super().__init__()
        from google import genai  # imported lazily so mock runs don't need the SDK
        from google.genai import types

        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise LLMError("GEMINI_API_KEY is not set")
        # A hard per-request timeout: an unattended job must never hang on one stuck call.
        self.client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=timeout_s * 1000))
        # separate budgets: embedding quota is far larger than generation quota, so embeds must
        # not queue behind (or use up) the scarce generation slots
        # Gemini quotas are per model, so each model gets its own limiter: the writer
        # (3.8-flash) and the editor (3.5-flash) do not queue behind each other.
        self.rpm = rpm
        self._limiters: dict[str, RateLimiter] = {}
        self._limiters_lock = threading.Lock()
        self.limiter = RateLimiter(rpm)
        self.embed_limiter = RateLimiter(embed_rpm)
        self.max_retries = max_retries
        self.exhausted: set[str] = set()
        self._tls = threading.local()   # per-thread call state: pages are generated in parallel

    @property
    def has_fallback(self) -> bool:
        return getattr(self._tls, "has_fallback", False)

    @has_fallback.setter
    def has_fallback(self, v: bool) -> None:
        self._tls.has_fallback = v

    @property
    def last_model(self) -> str | None:
        return getattr(self._tls, "last_model", None)

    @last_model.setter
    def last_model(self, v: str | None) -> None:
        self._tls.last_model = v

    def _limiter_for(self, model: str) -> RateLimiter:
        with self._limiters_lock:
            if model not in self._limiters:
                self._limiters[model] = RateLimiter(self.rpm)
            return self._limiters[model]

    def _models(self, spec: str) -> list[str]:
        """'a,b' -> try a, fall back to b once a's daily quota is gone (remembered for the run)."""
        models = [m.strip() for m in spec.split(",") if m.strip()]
        live = [m for m in models if m not in self.exhausted]
        if not live:
            raise QuotaExhausted(f"all models exhausted: {models}")
        return live

    def generate(self, *, task, model, system, prompt, schema, temperature, context=None, thinking=None):
        last: Exception | None = None
        models = self._models(model)
        for i, m in enumerate(models):
            self.has_fallback = i < len(models) - 1
            try:
                out = self._generate_one(task=task, model=m, system=system, prompt=prompt,
                                         schema=schema, temperature=temperature, thinking=thinking)
                self.last_model = m
                return out
            except QuotaExhausted as e:
                log.warning("%s: daily quota exhausted for %s, falling back", task, m)
                self.exhausted.add(m)
                last = e
            except ModelOverloaded as e:
                log.warning("%s: %s overloaded, falling back for this call", task, m)
                last = e
        raise last or QuotaExhausted(model)

    def _call(self, fn, what: str, limiter: RateLimiter | None = None):
        from google.genai import errors as gerrors

        day_429s = 0
        for attempt in range(self.max_retries + 1):
            (limiter or self.limiter).wait()
            try:
                return fn()
            except gerrors.APIError as e:  # covers ClientError / ServerError
                code = getattr(e, "code", None)
                if code == 429 and re.search(r"per_?day|per day", str(e).lower()):
                    day_429s += 1
                # a daily-quota 429 can still carry a short retryDelay; if waiting it out
                # didn't help twice, the day's quota really is gone -> next model
                if code == 429 and (_is_daily_quota(str(e)) or (day_429s >= 2 and self.has_fallback)):
                    log.warning("%s: quota error body: %s", what, str(e)[:600])
                    raise QuotaExhausted(f"{what}: daily quota exhausted") from e
                if code in (503, 504) and attempt >= 1 and self.has_fallback:
                    raise ModelOverloaded(f"{what}: HTTP {code} twice") from e
                if code not in self.RETRYABLE or attempt == self.max_retries:
                    raise LLMError(f"{what}: {code} {e}") from e
                delay = min(90, (2 ** attempt) * 3 + random.uniform(0, 2))
                # honour server-suggested retry delay when present
                suggested = _retry_delay(str(e))
                if suggested is not None:
                    delay = max(delay, suggested + 1)
                self.usage.retries += 1
                log.warning("%s: HTTP %s, retry %d in %.0fs", what, code, attempt + 1, delay)
                time.sleep(delay)
            except Exception as e:  # network errors, client-side timeouts
                if attempt == self.max_retries:
                    raise LLMError(f"{what}: {e}") from e
                if "timeout" in type(e).__name__.lower() and self.has_fallback:
                    raise ModelOverloaded(f"{what}: request timed out") from e
                self.usage.retries += 1
                time.sleep(min(60, 2 ** attempt * 2))
        raise LLMError(f"{what}: exhausted retries")

    def _generate_one(self, *, task, model, system, prompt, schema, temperature, thinking=None):
        from google.genai import types

        extra = {}
        # thinking: "low" = fast drafts / judging; None = the model's default (deeper) reasoning.
        # Older models that don't know thinking_level just get no thinking config.
        if thinking and not model.startswith("gemini-2"):
            extra["thinking_config"] = types.ThinkingConfig(thinking_level=thinking)
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            response_mime_type="application/json",
            response_schema=schema,
            **extra,
        )
        last_err = None
        for parse_attempt in range(2):  # one extra try if the JSON doesn't validate
            resp = self._call(
                lambda: self.client.models.generate_content(model=model, contents=prompt, config=config),
                f"{task}/{model}", self._limiter_for(model),
            )
            um = getattr(resp, "usage_metadata", None)
            self.usage.add(task, getattr(um, "prompt_token_count", 0) or 0,
                           getattr(um, "candidates_token_count", 0) or 0)
            try:
                if isinstance(resp.parsed, schema):
                    return resp.parsed
                return schema.model_validate(json.loads(resp.text))
            except (ValidationError, json.JSONDecodeError, TypeError) as e:
                last_err = e
                log.warning("%s: invalid JSON from model (attempt %d): %s", task, parse_attempt + 1, e)
        raise LLMError(f"{task}: model did not return valid {schema.__name__}: {last_err}")

    def embed(self, texts, *, model):
        last: Exception | None = None
        models = self._models(model)
        for i, m in enumerate(models):
            self.has_fallback = i < len(models) - 1
            try:
                return self._embed_one(texts, m)
            except QuotaExhausted as e:
                self.exhausted.add(m)
                last = e
        raise last or QuotaExhausted(model)

    def _embed_one(self, texts, model):
        from google.genai import types

        out = []
        for i in range(0, len(texts), 50):  # batch limit
            chunk = texts[i:i + 50]
            resp = self._call(
                lambda: self.client.models.embed_content(
                    model=model, contents=chunk,
                    config=types.EmbedContentConfig(task_type="SEMANTIC_SIMILARITY", output_dimensionality=768),
                ),
                f"embed/{model}", self.embed_limiter,
            )
            self.usage.embed_calls += 1
            out.extend(e.values for e in resp.embeddings)
        arr = np.asarray(out, dtype=np.float32)
        return arr / np.linalg.norm(arr, axis=1, keepdims=True).clip(min=1e-9)


# ============================================================================ Mock
def _h(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest()[:8], 16)


class MockProvider(Provider):
    """Offline, deterministic stand-in. Content is assembled from the keyword and the fact
    sheet, so pages differ by keyword, but it is obviously not production prose.

    misbehave="duplicate" makes the FIRST writer call for every page return a shared,
    near-identical body, so tests can check that QA rejects it and the reviser fixes it.
    """

    name = "mock"

    def __init__(self, misbehave: str | None = None, latency: dict | None = None):
        """latency: simulated seconds per task, e.g. {"write": 2.0, "judge": 0.5} - used by the
        benchmark to measure scheduling and call-count effects without spending API quota."""
        super().__init__()
        self.misbehave = misbehave
        self.latency = latency or {}
        self._writes: dict[str, int] = {}
        self._wlock = threading.Lock()

    def embed(self, texts, *, model):
        self.usage.embed_calls += 1
        if self.latency.get("embed"):
            time.sleep(self.latency["embed"])
        dim = 1024
        out = np.zeros((len(texts), dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in t.lower().split():
                out[i, _h(w) % dim] += 1.0
        return out / np.linalg.norm(out, axis=1, keepdims=True).clip(min=1e-9)

    def generate(self, *, task, model, system, prompt, schema, temperature, context=None, thinking=None):
        ctx = context or {}
        self.usage.add(task, len(prompt) // 4, 600)
        lat = self.latency.get(task, 0)
        if lat:
            time.sleep(lat * (0.35 if thinking == "low" else 1.0))
        if task == "plan":
            return self._plan(ctx, schema)
        if task in ("write", "revise"):
            return self._write(ctx, schema, revise=(task == "revise"))
        if task == "patch":
            from .models import PageContent

            full = self._write(ctx, PageContent, revise=True)
            return schema(**{f: getattr(full, f) for f in schema.model_fields})
        if task == "judge":
            return schema(specificity=5, usefulness=4, accuracy_risk=5, swap_test="pass", problems=[])
        raise LLMError(f"mock: unknown task {task}")

    def _plan(self, ctx, schema):
        from .models import PageBrief, ScopeExclusion

        briefs = []
        for rec in ctx["records"]:
            sib = rec.siblings[:2]
            briefs.append(PageBrief(
                slug=rec.slug,
                intent_type=rec.intent_guess,
                searcher_goal=f"Get '{rec.keyword}' done quickly and correctly.",
                scenario=f"Someone who needs to {rec.keyword} today.",
                unique_angle=f"Focus on {' '.join(rec.tokens)} specifically.",
                # mock questions carry a per-page token so they don't trip the FAQ-overlap gate;
                # real briefs get keyword-specific questions from the planner
                must_answer=[f"How to {rec.keyword} q{_h(rec.slug + str(i)) % 10**6}?"
                             for i in range(4)],
                exclusions=[ScopeExclusion(topic=s.replace('-', ' '), owner_slug=s) for s in sib],
                feature_ids=(rec.candidate_features or ["auto_edge", "share_export"])[:4],
                platform_fact_ids=["ios_notes_scan", "online_converters"],
                related_slugs=[r for r in ctx["neighbour_slugs"].get(rec.slug, [])][:3],
                ambiguity_note="",
            ))
        return schema(briefs=briefs)

    def _write(self, ctx, schema, revise: bool):
        from .models import CTA, FAQ, Alternative, FeaturePoint, Step

        rec, brief, app = ctx["record"], ctx["brief"], ctx["app"]
        with self._wlock:
            n = self._writes.get(rec.slug, 0)
            self._writes[rec.slug] = n + 1
        kw = rec.keyword
        rnd = random.Random(_h(rec.slug) + n)
        dup = self.misbehave == "duplicate" and not revise
        if dup:
            kw = "do this"  # identical body for every page -> QA must catch it
        words = rec.tokens
        feats = {f["id"]: f for f in app["features"]}

        def para(seed: str, k: int = 3) -> str:
            r = random.Random(_h(seed + kw + ("" if dup else str(n))))
            vocab = (["page", "scan", "file", "phone", "light", "edge", "text", "save"] if dup else
                     words + [w for f in brief.feature_ids for w in feats.get(f, {}).get("detail", "").lower().split()[:12]])
            sents = []
            for j in range(k):
                picked = " ".join(r.choice(vocab) for _ in range(9))
                sents.append(f"When you {kw}, the detail that matters most is {picked} ({seed} {j}).")
            return " ".join(sents)

        return schema(
            title=f"{rec.keyword.title()} - Step-by-Step Guide"[:60],
            meta_description=(f"Learn how to {kw} with clear steps, free built-in options, common mistakes "
                              f"and fixes. Updated guide for {rec.platform} users.")[:155].ljust(115, "."),
            h1=f"How to {rec.keyword}" if rec.intent_guess != "comparison" else rec.keyword.capitalize(),
            quick_answer=para("quick", 2),
            intro=[para("intro-a"), para("intro-b")],
            steps_heading=f"How to {kw} with {app['name']}",
            steps=[Step(title=f"Step {i + 1} for {kw}", detail=para(f"step{i}", 2)) for i in range(5)],
            alternatives_heading=f"Other ways to {kw}",
            alternatives=[Alternative(method=f"Option {i} for {kw}", how=para(f"alt{i}", 1),
                                      best_for=para(f"bf{i}", 1), limitation=para(f"lim{i}", 1)) for i in range(3)],
            features_heading="Features that help",
            feature_points=[FeaturePoint(feature_id=f, benefit=para(f"feat-{f}", 1).replace("free", "handy")
                                         if feats.get(f, {}).get("tier") == "pro" else para(f"feat-{f}", 1))
                            for f in brief.feature_ids[:5]],
            tips_heading=f"Tips for {kw}",
            tips=[para(f"tip{i}", 1) for i in range(4)],
            faqs=[FAQ(question=q, answer=para(f"faq{i}", 2)) for i, q in enumerate(brief.must_answer)],
            cta=CTA(heading=f"Ready to {kw}?", text=para("cta", 1)),
        )


def make_provider(cfg) -> Provider:
    name = os.environ.get("PSEO_PROVIDER") or cfg["llm"]["provider"]
    if name == "mock":
        return MockProvider(misbehave=os.environ.get("PSEO_MOCK_MISBEHAVE"))
    if name == "gemini":
        return GeminiProvider(rpm=cfg["llm"]["requests_per_minute"], max_retries=cfg["llm"]["max_retries"],
                              embed_rpm=cfg["llm"].get("embed_requests_per_minute", 60),
                              timeout_s=cfg["llm"]["request_timeout_s"])
    raise LLMError(f"unknown provider {name}")
