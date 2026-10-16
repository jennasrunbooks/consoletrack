"""Service-layer errors. `app/api/errors.py` maps each to an HTTP status and an `Error` body."""

from __future__ import annotations

from enum import StrEnum


class InvalidTransition(Exception):
    """The event isn't allowed from the subject's current state (HTTP 409)."""

    def __init__(self, current: StrEnum, event: StrEnum) -> None:
        self.current = current
        self.event = event
        super().__init__(f"{event.value} is not allowed from {current.value}")


class MissingReason(ValueError):
    """A decision that needs a note or reason (Accept, Reject) arrived without one (HTTP 422)."""


class NotFound(Exception):
    """No such change or incident (HTTP 404)."""

    def __init__(self, subject: str, subject_id: int) -> None:
        self.subject = subject
        self.subject_id = subject_id
        super().__init__(f"{subject} {subject_id} not found")
