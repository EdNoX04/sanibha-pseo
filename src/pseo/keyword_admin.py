"""Manage the keyword list from the command line instead of editing the CSV by hand.

    ./run.sh keywords                         list keywords and their page status
    ./run.sh add "scan receipts to Excel"     add one or more keywords
    ./run.sh remove "scan receipts to Excel"  remove by keyword or slug (its page is retired next run)
    ./run.sh replace "old keyword" "new one"  swap one keyword for another

Add `--run` to any of them to generate straight away. The CSV stays the source of truth, so
the scheduled job and a manual edit of data/keywords.csv keep working exactly as before.
"""
from __future__ import annotations

import csv
from pathlib import Path

from .keywords import content_tokens, normalise, slugify


def _read(path: Path) -> list[dict]:
    with path.open(newline="") as fh:
        return [r for r in csv.DictReader(fh) if (r.get("keyword") or "").strip()]


def _write(path: Path, rows: list[dict]) -> None:
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["keyword", "app_id"])
        w.writeheader()
        for r in rows:
            w.writerow({"keyword": r["keyword"].strip(), "app_id": r["app_id"].strip()})
    tmp.replace(path)  # atomic: a crash never leaves a half-written keyword list


def _key(kw: str, app_id: str) -> tuple:
    return app_id, " ".join(sorted(content_tokens(kw)))


def _find(rows: list[dict], ref: str) -> int | None:
    ref_n = normalise(ref)
    for i, r in enumerate(rows):
        if normalise(r["keyword"]) == ref_n or slugify(r["keyword"]) == ref_n:
            return i
    return None


def add(path: Path, keywords: list[str], app_id: str, apps: dict) -> list[str]:
    if app_id not in apps:
        raise ValueError(f"unknown app '{app_id}'. Known apps: {', '.join(apps)}")
    rows, msgs = _read(path), []
    seen = {_key(r["keyword"], r["app_id"]): r["keyword"] for r in rows}
    for kw in keywords:
        kw = " ".join(kw.split())
        if not content_tokens(kw):
            msgs.append(f"skipped '{kw}': no meaningful words")
            continue
        k = _key(kw, app_id)
        if k in seen:
            msgs.append(f"skipped '{kw}': same words as existing keyword '{seen[k]}'")
            continue
        rows.append({"keyword": kw, "app_id": app_id})
        seen[k] = kw
        msgs.append(f"added '{kw}'  ->  /{slugify(kw)}")
    _write(path, rows)
    return msgs


def remove(path: Path, refs: list[str]) -> list[str]:
    rows, msgs = _read(path), []
    for ref in refs:
        i = _find(rows, ref)
        if i is None:
            msgs.append(f"not found: '{ref}' (run `./run.sh keywords` to list them)")
            continue
        msgs.append(f"removed '{rows.pop(i)['keyword']}' (its page is retired on the next run)")
    _write(path, rows)
    return msgs


def replace(path: Path, old: str, new: str, apps: dict) -> list[str]:
    rows = _read(path)
    i = _find(rows, old)
    if i is None:
        return [f"not found: '{old}' (run `./run.sh keywords` to list them)"]
    app_id = rows[i]["app_id"]
    msgs = remove(path, [old])
    msgs += add(path, [new], app_id, apps)
    return msgs


def listing(path: Path, store) -> str:
    pages = {p["slug"]: p for p in store.pages()}
    rows = _read(path)
    out = [f"{len(rows)} keywords in {path.name}:", ""]
    for r in rows:
        s = slugify(r["keyword"])
        status = pages[s]["status"] if s in pages else "not generated yet"
        out.append(f"  {status:<18} {r['keyword']}  ({r['app_id']})")
    retired = [p for p in pages.values() if p["status"] == "retired"]
    if retired:
        out += ["", "Retired (removed from the list, no longer published):"]
        out += [f"  {p['keyword']}" for p in retired]
    return "\n".join(out)
