class CtlError(Exception):
    """Recoverable controller error shown to the operator."""


class VerificationError(CtlError):
    """Package bytes do not match the approved set."""


class StateError(CtlError):
    """Persistent state or machine transition is invalid."""


class PowerCut(Exception):
    """Raised in-process to simulate an abrupt power loss after durable I/O."""

    def __init__(self, phase: str, exit_code: int = 42):
        super().__init__(f"simulated power loss after {phase}")
        self.phase = phase
        self.exit_code = exit_code
