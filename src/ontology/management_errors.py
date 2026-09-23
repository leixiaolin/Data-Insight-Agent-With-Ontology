"""Public, non-sensitive management failures."""
from typing import Any


class ManagementError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, **details: Any):
        super().__init__(message)
        self.status = status
        self.detail = {"code": code, "message": message, **details}
