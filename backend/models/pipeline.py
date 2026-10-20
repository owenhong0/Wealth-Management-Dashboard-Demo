"""One row per pipeline run: timing, status and row counts."""
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, str_enum, utcnow
from models.enums import RunStatus


class PipelineRun(Base):
    __tablename__ = "pipeline_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[RunStatus] = mapped_column(str_enum(RunStatus), default=RunStatus.RUNNING)
    # e.g. {"transactions": 84, "exceptions": 4, "step_seconds": {...}}
    counts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()