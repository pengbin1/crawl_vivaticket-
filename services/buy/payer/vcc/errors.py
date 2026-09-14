from __future__ import annotations


class VccError(Exception):
    """Base VCC client / flow error."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        http_status: int | None = None,
        trace_id: str = "",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.trace_id = trace_id
        self.retryable = retryable


class VccEnvMismatch(VccError):
    pass


class VccManualRequired(VccError):
    """Stop automation; keep original application IDs for ops."""
