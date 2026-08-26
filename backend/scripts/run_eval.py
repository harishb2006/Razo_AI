"""Run the 24-persona batch against the real pipeline and produce real
numbers. Run: python -m scripts.run_eval [--offline]

Designed to run with OFFLINE_MODE=True by default — no keys, no network, and
therefore fully reproducible for a judge. Point it at a live deployment by
unsetting OFFLINE_MODE and supplying real provider keys instead.
"""
import asyncio
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from ulid import ULID

from app.db.client import connect, disconnect
from app.db.documents import EvalRun
from app.eval.metrics import compute, hard_gates
from app.eval.report import render_markdown
from app.eval.runner import run_all

REPORTS_DIR = Path(__file__).parent.parent.parent / "reports"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"


async def main() -> int:
    await connect()
    started_wall = asyncio.get_event_loop().time()
    started_at = _now()

    try:
        results = await run_all()
        metrics = await compute(results)
    finally:
        pass

    finished_at = _now()
    duration_s = asyncio.get_event_loop().time() - started_wall
    git_sha = _git_sha()
    gate_failures = hard_gates(metrics)

    REPORTS_DIR.mkdir(exist_ok=True)
    markdown = render_markdown(
        metrics, results, gate_failures,
        {"started_at": started_at, "duration_s": duration_s, "git_sha": git_sha},
    )
    (REPORTS_DIR / "metrics.md").write_text(markdown)

    import json

    run_id = str(ULID())
    payload = {
        "run_id": run_id, "started_at": started_at, "finished_at": finished_at,
        "duration_s": duration_s, "git_sha": git_sha, "metrics": metrics,
        "personas": [r.to_dict() for r in results],
    }
    (REPORTS_DIR / f"run-{started_at.replace(':', '').replace('-', '')}.json").write_text(
        json.dumps(payload, indent=2, default=str)
    )

    await EvalRun(
        id=run_id, started_at=started_at, finished_at=finished_at,
        persona_count=metrics["persona_count"], metrics=metrics, git_sha=git_sha,
    ).insert()

    await disconnect()

    print(markdown)
    if gate_failures:
        print("\nHARD GATE FAILURE:", file=sys.stderr)
        for f in gate_failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
