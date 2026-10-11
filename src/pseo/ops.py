"""Unattended-operation helpers: run lock, alerts, dead-man's-switch heartbeat.

The question "would you know if it broke?" has three different failure shapes:
  1. the run crashes                 -> exception handler sends an alert + heartbeat /fail
  2. the run "succeeds" with bad output -> quality thresholds turn it into a degraded run + alert
  3. the run never starts at all      -> nothing in-process can report that, so a heartbeat
     service (e.g. healthchecks.io) alerts when the expected ping does NOT arrive.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import urllib.request
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger("pseo.ops")


class AlreadyRunning(RuntimeError):
    pass


@contextmanager
def run_lock(path: Path):
    """Exclusive, non-blocking lock. The OS releases it if the process dies, so no stale locks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as e:
        fh.close()
        raise AlreadyRunning(f"another run holds {path}") from e
    try:
        fh.write(str(os.getpid()))
        fh.flush()
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def _post(url: str, payload: dict | None = None, timeout: int = 10) -> None:
    data = json.dumps(payload).encode() if payload is not None else b""
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=timeout).read()
    except Exception as e:  # alerting must never crash the run
        log.warning("could not reach %s: %s", url.split("?")[0], e)


def alert(cfg, title: str, body: str) -> None:
    url = os.environ.get(cfg["ops"]["alert_webhook_env"])
    log.warning("ALERT: %s\n%s", title, body)
    if url:
        # {"text": ...} is accepted by Slack incoming webhooks; Discord accepts {"content": ...}
        _post(url, {"text": f"*{title}*\n{body}", "content": f"**{title}**\n{body}"[:1900]})


def heartbeat(cfg, state: str = "") -> None:
    """state: '' (success), 'start', or 'fail' - healthchecks.io URL conventions."""
    url = os.environ.get(cfg["ops"]["heartbeat_url_env"])
    if url:
        _post(url.rstrip("/") + (f"/{state}" if state else ""))
