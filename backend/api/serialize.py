"""Model -> JSON helpers. Enums become their string values, dates
YYYY-MM-DD, datetimes ISO 8601 in UTC, Decimals exact strings."""
import enum
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from flask.json.provider import DefaultJSONProvider

from models import Decision, PipelineRun
from pipeline.schema import ReconException, SourceRef


def iso(value: date | datetime | None) -> str | None:
    """Stored datetimes are naive UTC (SQLite has no time zones); mark them UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return value.isoformat()


def enum_value(value: enum.Enum | str | None) -> str | None:
    return value.value if isinstance(value, enum.Enum) else value


class ApiJSONProvider(DefaultJSONProvider):
    """Anything that slips past the serializers still comes out as ISO,
    enums as their values, and Decimals as exact strings (never floats:
    money must not pick up binary rounding on the way to the browser)."""

    sort_keys = False

    @staticmethod
    def default(o: Any) -> Any:
        if isinstance(o, (datetime, date)):
            return iso(o)
        if isinstance(o, enum.Enum):
            return o.value
        if isinstance(o, Decimal):
            return str(o)
        return DefaultJSONProvider.default(o)


# --- pipeline runs ------------------------------------------------------------

def run(r: PipelineRun) -> dict[str, Any]:
    duration = r.duration_seconds
    return {
        "id": r.id,
        "status": enum_value(r.status),
        "started_at": iso(r.started_at),
        "finished_at": iso(r.finished_at),
        "duration_seconds": round(duration, 2) if duration is not None else None,
        "counts": r.counts or {},
        "error": r.error,
    }


# --- review -------------------------------------------------------------------

def source(ref: SourceRef) -> dict[str, Any]:
    return {**ref.model_dump(mode="json"), "label": ref.label()}


def exception(e: ReconException) -> dict[str, Any]:
    body = e.model_dump(mode="json", exclude={"evidence"})
    body["evidence"] = [source(r) for r in e.evidence]
    return body


def decision(d: Decision) -> dict[str, Any]:
    return {
        "id": d.id,
        "exception_id": d.exception_id,
        "exception_type": d.exception_type,
        "status": enum_value(d.status),
        "decided_by": d.decided_by,
        "note": d.note,
        "summary": d.summary,
        "proposed_fix": d.proposed_fix,
        "fix": d.fix,
        "decided_at": iso(d.decided_at),
        "voided_at": iso(d.voided_at),
    }