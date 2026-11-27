"""Errors with stable machine-readable codes for the access layer CLI."""

from __future__ import annotations

from typing import Any, Dict


class StationError(Exception):
    code = "STATION_ERROR"
    exit_code = 1

    def __init__(
        self,
        message: str,
        *,
        code_override: str | None = None,
        exit_code_override: int | None = None,
        **details: Any,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code_override:
            self.code = code_override
        if exit_code_override is not None:
            self.exit_code = exit_code_override
        self.details = details

    def to_dict(self) -> Dict[str, Any]:
        body = {"ok": False, "code": self.code, "error": self.message}
        body.update(self.details)
        return body


class ValidationError(StationError):
    code = "VALIDATION_ERROR"
    exit_code = 2


class FullError(StationError):
    code = "CABINET_FULL"
    exit_code = 3


class UnauthorizedError(StationError):
    code = "UNAUTHORIZED"
    exit_code = 4


class TokenError(UnauthorizedError):
    code = "INVALID_TOKEN"


class ReportMismatchError(UnauthorizedError):
    code = "REPORT_CHANNEL_MISMATCH"


class ConflictError(StationError):
    code = "CONFLICT"
    exit_code = 5


class NotFoundError(StationError):
    code = "NOT_FOUND"
    exit_code = 6


class ChannelBindingError(StationError):
    code = "CHANNEL_MIGRATION_REQUIRED"
    exit_code = 7


class StateError(StationError):
    code = "STATE_ERROR"
    exit_code = 8
