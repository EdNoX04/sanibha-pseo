"""Command line entry point.

    python -m pseo run                 # incremental run (only new/changed keywords)
    python -m pseo run --force         # regenerate everything
    python -m pseo run --only sign-pdf-on-iphone-free
    python -m pseo status              # last runs + page states
    python -m pseo render              # re-render HTML/MD + report from state, no LLM calls
    python -m pseo explain <slug>      # the full story of one page: inputs, plan, drafts, verdicts
    python -m pseo keywords            # list keywords; add / remove / replace edit the list (--run to apply)

Exit codes (what a scheduler / CI reads):
    0 ok   1 crashed or aborted (circuit breaker)   2 degraded (quality threshold breached or pages failed)   3 already running
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from .config import ROOT, load_config


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader (KEY=VALUE lines). Real secrets in production come from the
    scheduler's secret store (GitHub Actions secrets), never from the repo."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pseo", description="Programmatic SEO agent")
    ap.add_argument("--config", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="generate pages")
    r.add_argument("--force", action="store_true", help="regenerate even unchanged pages")
    r.add_argument("--only", nargs="*", help="slugs or keywords to (re)generate")
    r.add_argument("--provider", choices=["gemini", "mock"], help="override llm.provider")
    sub.add_parser("status", help="show recent runs and page states")
    sub.add_parser("render", help="re-render pages + report from stored state (no LLM calls)")
    ex = sub.add_parser("explain", help="show how one page was made: record, brief, every draft and verdict")
    ex.add_argument("page", nargs="?", help="slug or keyword (omit to list pages)")
    sub.add_parser("keywords", help="list keywords and their page status")
    ad = sub.add_parser("add", help="add keyword(s) to data/keywords.csv")
    ad.add_argument("keyword", nargs="+")
    ad.add_argument("--app", default=None, help="app id (default: the only app, or doc-scanner)")
    ad.add_argument("--run", action="store_true", help="generate right away")
    rm = sub.add_parser("remove", help="remove keyword(s) by keyword or slug; their pages are retired")
    rm.add_argument("keyword", nargs="+")
    rm.add_argument("--run", action="store_true", help="apply right away")
    rp = sub.add_parser("replace", help="swap one keyword for another")
    rp.add_argument("old")
    rp.add_argument("new")
    rp.add_argument("--run", action="store_true", help="generate right away")
    args = ap.parse_args(argv)

    _load_dotenv(Path(os.environ.get("PSEO_ENV_FILE", ROOT / ".env")))
    logging.basicConfig(level=os.environ.get("PSEO_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    for noisy in ("httpx", "google_genai", "google_genai.models"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if getattr(args, "provider", None):
        os.environ["PSEO_PROVIDER"] = args.provider
    cfg = load_config(args.config)
    if os.environ.get("PSEO_PROVIDER"):
        cfg.raw["llm"]["provider"] = os.environ["PSEO_PROVIDER"]

    from .ops import AlreadyRunning
    from .store import Store

    if args.cmd == "status":
        st = Store(cfg.path("state_db"))
        for run in st.recent_runs():
            print(json.dumps(run))
        counts = {}
        for p in st.pages():
            counts[p["status"]] = counts.get(p["status"], 0) + 1
        print("pages:", counts)
        return 0

    if args.cmd in ("keywords", "add", "remove", "replace"):
        from . import keyword_admin as ka
        from .keywords import load_app_facts

        kw_path = cfg.path("keywords")
        apps = load_app_facts(cfg.path("apps_dir"))
        if args.cmd == "keywords":
            print(ka.listing(kw_path, Store(cfg.path("state_db"))))
            return 0
        try:
            if args.cmd == "add":
                app_id = args.app or (next(iter(apps)) if len(apps) == 1 else "doc-scanner")
                msgs = ka.add(kw_path, args.keyword, app_id, apps)
            elif args.cmd == "remove":
                msgs = ka.remove(kw_path, args.keyword)
            else:
                msgs = ka.replace(kw_path, args.old, args.new, apps)
        except ValueError as e:
            print(f"error: {e}")
            return 2
        print("\n".join(msgs))
        if not args.run:
            print("\nNext: ./run.sh run   (only the changed keywords are processed)")
            return 0
        args.force, args.only = False, None   # fall through to a normal incremental run

    if args.cmd == "explain":
        from .explain import explain
        print(explain(Store(cfg.path("state_db")), args.page))
        return 0

    if args.cmd == "render":
        from .keywords import load_app_facts
        from .render import render_all
        from .report import write_report

        st = Store(cfg.path("state_db"))
        rendered = render_all(st, cfg, load_app_facts(cfg.path("apps_dir")))
        last = (st.recent_runs(1) or [{"id": 0, "status": "n/a"}])[0]
        write_report(st, cfg, last["id"], {"status": f"re-rendered (last run: {last['status']})",
                                           "rendered": rendered, "usage": {}}, json.loads(last.get("stats_json") or "{}"))
        print(json.dumps(rendered))
        return 0

    from .llm import make_provider
    from .pipeline import Pipeline

    try:
        result = Pipeline(cfg, make_provider(cfg)).run(force=args.force, only=args.only)
    except AlreadyRunning as e:
        logging.error("%s", e)
        return 3
    except Exception:
        logging.exception("run crashed")
        return 1
    print(json.dumps({k: result[k] for k in ("status", "attempted", "rendered", "duration_s")}, indent=1))
    return {"ok": 0, "aborted": 1}.get(result["status"], 2)


if __name__ == "__main__":
    sys.exit(main())
