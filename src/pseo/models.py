"""Data schema for the pipeline.

Three layers, each derived from the previous one:

1. KeywordRecord  - deterministic, derived from the raw keyword with rules (no LLM).
2. PageBrief      - planner agent output: intent, angle, scope boundaries vs sibling pages.
3. PageContent    - writer agent output: the page itself, section by section.

LLM-facing models (PageBrief, PageContent, JudgeVerdict) only use types that Gemini's
structured-output mode supports (str, int, list, nested models, Literal enums).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

IntentType = Literal[
    "task_howto",        # "scan receipt on iPhone": do a task on a device
    "conversion",        # "convert PDF to Word free": change format A -> B
    "no_app_alternative",  # "scan document without an app": wants to avoid installing
    "comparison",        # "best free PDF scanner app": evaluating options
    "problem_fix",       # "remove shadow from scanned document": fixing a bad result
]


# --------------------------------------------------------------------------- layer 1
class KeywordRecord(BaseModel):
    keyword: str                      # exactly as given
    app_id: str
    slug: str                         # URL path, derived from keyword
    tokens: list[str]                 # normalised content words (stopwords removed)
    platform: str                     # "iphone" | "any"
    wants_free: bool                  # keyword carries a price modifier ("free")
    intent_guess: IntentType          # rule-based guess; planner may override
    action: str                       # leading verb: scan / convert / combine ...
    source_format: str | None = None  # e.g. "pdf", "image", "paper"
    target_format: str | None = None  # e.g. "word", "text", "contacts"
    candidate_features: list[str] = Field(default_factory=list)  # feature ids matched by trigger terms
    # filled by the clustering step
    cluster_id: int = -1
    siblings: list[str] = Field(default_factory=list)       # slugs that must be differentiated
    merged_keywords: list[str] = Field(default_factory=list)  # near-identical keywords folded in


# --------------------------------------------------------------------------- layer 2
class ScopeExclusion(BaseModel):
    topic: str = Field(description="Topic this page must NOT cover in depth")
    owner_slug: str = Field(description="Slug of the sibling page that owns this topic; link to it instead")


class PageBrief(BaseModel):
    slug: str
    intent_type: IntentType
    searcher_goal: str = Field(description="One sentence: what the person typing this keyword is trying to get done")
    scenario: str = Field(description="A concrete real-life situation this searcher is in")
    unique_angle: str = Field(description="What this page offers that its sibling pages do not")
    must_answer: list[str] = Field(description="4-6 specific questions this page must answer")
    exclusions: list[ScopeExclusion] = Field(description="Topics owned by sibling pages")
    feature_ids: list[str] = Field(description="3-5 app feature ids genuinely relevant to this task")
    platform_fact_ids: list[str] = Field(description="Platform fact ids useful for the alternatives section")
    related_slugs: list[str] = Field(description="2-4 slugs of pages worth linking to")
    ambiguity_note: str = Field(description="If the keyword can mean two things, say how the page handles both; else empty string")


class PlanBatch(BaseModel):
    briefs: list[PageBrief]


# --------------------------------------------------------------------------- layer 3
class Step(BaseModel):
    title: str
    detail: str


class Alternative(BaseModel):
    method: str
    how: str
    best_for: str
    limitation: str


class FeaturePoint(BaseModel):
    feature_id: str
    benefit: str = Field(description="Why this feature matters for THIS task specifically")


class FAQ(BaseModel):
    question: str
    answer: str


class CTA(BaseModel):
    heading: str
    text: str


class PageContent(BaseModel):
    title: str = Field(description="SEO title, <= 60 characters")
    meta_description: str = Field(description="110-155 characters")
    h1: str
    quick_answer: str = Field(description="40-60 word direct answer, written to be quoted as a featured snippet")
    intro: list[str] = Field(description="2-3 short paragraphs")
    steps_heading: str
    steps: list[Step] = Field(description="3-8 steps using the app")
    alternatives_heading: str
    alternatives: list[Alternative] = Field(description="2-4 other ways to do it, including free built-in options")
    features_heading: str
    feature_points: list[FeaturePoint] = Field(description="3-5 items")
    tips_heading: str
    tips: list[str] = Field(description="3-5 task-specific tips or fixes")
    faqs: list[FAQ] = Field(description="4-6 items")
    cta: CTA


class JudgeVerdict(BaseModel):
    specificity: int = Field(description="1-5: would this page only make sense for this exact keyword?")
    usefulness: int = Field(description="1-5: does it fully solve the searcher's task?")
    accuracy_risk: int = Field(description="1-5 where 5 = no unsupported or risky claims")
    swap_test: str = Field(description="'pass' if swapping in a sibling keyword would make the page wrong, 'fail' if it would still read fine")
    problems: list[str] = Field(description="Concrete problems to fix, empty if none")


# --------------------------------------------------------------------------- record of a page
class QAResult(BaseModel):
    passed: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)
    overlaps: list[str] = Field(default_factory=list)   # verbatim sentences shared with other pages
    # Which PageContent fields the errors live in. When every error is local to a few fields the
    # reviser patches just those fields (small, fast call); page-level errors force a full rewrite.
    fix_fields: list[str] = Field(default_factory=list)
    page_level: bool = False
