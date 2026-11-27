"""统一异常类型。"""

from __future__ import annotations

from typing import Any, Dict, Optional


class SealChannelError(Exception):
    """所有可预期的业务错误。"""

    code = "SEAL_CHANNEL_ERROR"

    def __init__(
        self,
        message: str,
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": False,
            "error": self.message,
            "code": self.code,
            "details": self.details,
        }


class ValidationError(SealChannelError):
    code = "VALIDATION_ERROR"


class AllocationError(SealChannelError):
    code = "ALLOCATION_ERROR"


class DepartureError(SealChannelError):
    code = "DEPARTURE_ERROR"


class SealConsumedError(SealChannelError):
    code = "SEAL_ALREADY_CONSUMED"


class SendOrderError(SealChannelError):
    code = "SEND_ORDER_ERROR"


class ReconciliationError(SealChannelError):
    code = "RECONCILIATION_ERROR"
