"""Declarative base, shared mixins and enum helper."""
import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Predictable constraint names keep the schema readable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """Naive UTC. SQLite drops time zones, so every timestamp is stored as
    naive UTC and the API adds the zone back when serializing."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


def str_enum(enum_cls: type[enum.Enum]) -> Enum:
    """Store enums as VARCHAR values, not native database enums."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=40,
        values_callable=lambda e: [m.value for m in e],
    )