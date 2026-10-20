"""Model -> JSON helpers. Enums become their string values, dates
YYYY-MM-DD, datetimes ISO 8601 in UTC."""
import enum
from datetime import date, datetime, timezone
from typing import Any

from flask.json.provider import DefaultJSONProvider

from models import PipelineRun


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
    and enums as their values."""

    sort_keys = False

    @staticmethod
    def default(o: Any) -> Any:
        if isinstance(o, (datetime, date)):
            return iso(o)
        if isinstance(o, enum.Enum):
            return o.value
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