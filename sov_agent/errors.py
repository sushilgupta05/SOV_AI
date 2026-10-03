"""User-facing errors (NFR-4): every failure carries a friendly message."""


class IngestionError(Exception):
    """Raised for any file the system cannot ingest.

    `user_message` is safe to show in the UI; `detail` is for internal logs only.
    """

    def __init__(self, code: str, user_message: str, detail: str | None = None):
        super().__init__(f"[{code}] {user_message}")
        self.code = code
        self.user_message = user_message
        self.detail = detail


class StageNotValidatedError(Exception):
    """Raised when a stage runs before its prerequisite stage was validated (NFR-7)."""
