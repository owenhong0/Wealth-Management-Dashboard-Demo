"""Enum values shared by models, pipeline and API.

Each inherits from str, so values serialize straight to JSON.
"""
import enum


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class DecisionStatus(str, enum.Enum):
    APPROVED = "APPROVED"
    OVERRIDDEN = "OVERRIDDEN"