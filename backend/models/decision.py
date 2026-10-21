"""Human decisions on exceptions: the only data the app stores.

Everything else (the ledger, exceptions, basis, P&L) is recomputed from the
banks' files on every request, with these decisions applied. That keeps one
source of truth: the books always trace back to source lines, and the only
thing a person adds is a logged decision.

The table is append-only. A newer decision on the same exception supersedes
the older one; a demo reset sets `voided_at` instead of deleting, so the
audit trail survives.
"""
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, str_enum, utcnow
from models.enums import DecisionStatus


class Decision(Base):
    __tablename__ = "decision"

    id: Mapped[int] = mapped_column(primary_key=True)
    exception_id: Mapped[str] = mapped_column(String(120), index=True)
    exception_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[DecisionStatus] = mapped_column(str_enum(DecisionStatus))
    decided_by: Mapped[str] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text)
    # What the reviewer saw when deciding: the proposal and the fix that was applied.
    summary: Mapped[str] = mapped_column(Text)
    proposed_fix: Mapped[str | None] = mapped_column(Text)
    fix: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime)