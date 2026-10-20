"""Local pipeline runner. Rebuilds the database from data/generated/.

    cd backend && source .venv/bin/activate
    python -m pipeline.run

Every step is a plain function `dict -> dict`: it reads what it needs from
the event and adds its outputs. The runner records a pipeline_run row with
timing and counts, the same pattern as the antibody monitor.
"""
from __future__ import annotations

import logging
import sys
import time
import traceback
from typing import Any, Callable

from db import reset_schema, session_scope
from models import PipelineRun, RunStatus
from models.base import utcnow

log = logging.getLogger("pipeline")

Step = Callable[[dict[str, Any]], dict[str, Any]]

# Filled in as each step is built:
# parse_harbour, parse_meridian, parse_straits, normalize, load,
# reconcile, basis, pnl, propose_fixes
STEPS: list[Step] = []


def start_run() -> int:
    with session_scope() as session:
        run = PipelineRun(status=RunStatus.RUNNING, counts={})
        session.add(run)
        session.flush()
        return run.id


def finish_run(run_id: int, status: RunStatus, counts: dict[str, Any], error: str | None = None) -> None:
    with session_scope() as session:
        run = session.get(PipelineRun, run_id)
        run.status = status
        run.counts = counts
        run.error = error
        run.finished_at = utcnow()


def summarize(event: dict[str, Any]) -> dict[str, Any]:
    """Every value a step put on the event under `count_*`, plus timings."""
    counts = {k.removeprefix("count_"): v for k, v in event.items() if k.startswith("count_")}
    counts["step_seconds"] = event.get("step_seconds", {})
    return counts


def run_pipeline() -> int:
    reset_schema()  # every run rebuilds from the generated files
    run_id = start_run()
    log.info("run %d started (%d steps)", run_id, len(STEPS))

    event: dict[str, Any] = {"run_id": run_id, "step_seconds": {}}
    for step in STEPS:
        name = step.__name__
        started = time.monotonic()
        try:
            event = step(event)
        except Exception as exc:
            log.exception("run %d failed in %s", run_id, name)
            event["step_seconds"][name] = round(time.monotonic() - started, 2)
            finish_run(run_id, RunStatus.FAILED, summarize(event),
                       error=f"{name}: {exc}\n{traceback.format_exc(limit=5)}")
            return run_id
        event["step_seconds"][name] = round(time.monotonic() - started, 2)
        log.info("run %d: %s done in %.2fs", run_id, name, event["step_seconds"][name])

    finish_run(run_id, RunStatus.SUCCEEDED, summarize(event))
    log.info("run %d succeeded: %s", run_id, summarize(event))
    return run_id


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    run_id = run_pipeline()
    with session_scope() as session:
        status = session.get(PipelineRun, run_id).status
    sys.exit(0 if status == RunStatus.SUCCEEDED else 1)


if __name__ == "__main__":
    main()