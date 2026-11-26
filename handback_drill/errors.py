class DrillError(Exception):
    """Rule violation or bad request in the drill backend."""

    def __init__(self, code: str, message: str, status: int = 409, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}

    def to_dict(self):
        return {
            "ok": False,
            "error": self.code,
            "message": self.message,
            "details": self.details,
        }
