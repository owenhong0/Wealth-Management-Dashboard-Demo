"""Local pipeline runner: runs every step from data/generated/ and logs the run.

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

from db import create_tables, session_scope
from models import PipelineRun, RunStatus
from models.base import utcnow
from pipeline.parse_csv import parse_harbour
from pipeline.parse_pdf import parse_meridian
from pipeline.decisions import apply_decisions
from pipeline.normalize import normalize
from pipeline.parse_swift import parse_straits
from pipeline.pnl import basis_and_pnl
from pipeline.reconcile import reconcile
from pipeline.secmaster import resolve_securities

log = logging.getLogger("pipeline")

Step = Callable[[dict[str, Any]], dict[str, Any]]

# Still to add: load (after basis_and_pnl), propose_fixes (after reconcile)
STEPS: list[Step] = [parse_harbour, parse_meridian, parse_straits, resolve_securities, normalize,
                     reconcile, apply_decisions, basis_and_pnl]


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
    """Every int/dict a step put on the event under `count_*`, plus timings."""
    counts = {k.removeprefix("count_"): v for k, v in event.items() if k.startswith("count_")}
    counts["step_seconds"] = event.get("step_seconds", {})
    return counts


class StepFailed(RuntimeError):
    def __init__(self, step: str, event: dict[str, Any], cause: Exception):
        super().__init__(f"{step}: {cause}")
        self.step, self.event, self.cause = step, event, cause


def run_steps(event: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run every step in memory, no database. Used by the runner, the API and tests.

    Pass {"decisions": {exception_id: {"status": "APPROVED", ...}}} to apply decisions.
    """
    event = {"step_seconds": {}, **(event or {})}
    for step in STEPS:
        name = step.__name__
        started = time.monotonic()
        try:
            event = step(event)
        except Exception as exc:
            event["step_seconds"][name] = round(time.monotonic() - started, 2)
            raise StepFailed(name, event, exc) from exc
        event["step_seconds"][name] = round(time.monotonic() - started, 2)
        log.info("%s done in %.2fs", name, event["step_seconds"][name])
    return event


def run_pipeline() -> int:
    create_tables()  # never drops tables: the decision log must survive re-runs
    run_id = start_run()
    log.info("run %d started (%d steps)", run_id, len(STEPS))
    try:
        event = run_steps({"run_id": run_id})
    except StepFailed as failed:
        log.exception("run %d failed in %s", run_id, failed.step)
        finish_run(run_id, RunStatus.FAILED, summarize(failed.event),
                   error=f"{failed}\n{traceback.format_exc(limit=5)}")
        return run_id

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