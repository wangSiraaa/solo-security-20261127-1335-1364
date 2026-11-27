class StudioError(Exception):
    """Base class for expected studio errors."""


class PackageFormatError(StudioError):
    """A declarative package is not valid JSON or does not match the schema."""


class PackageQuotaError(StudioError):
    """The package exceeds the fixed import memory/shape quota."""


class UnknownPrimitiveError(PackageFormatError):
    """A reference points outside the package or to a missing primitive."""


class StaleAdmissionError(StudioError):
    """An admission token does not refer to the currently imported revision."""

    def __init__(self, admission_id: str, package_id: str, current_hash: str | None):
        self.admission_id = admission_id
        self.package_id = package_id
        self.current_hash = current_hash
        super().__init__(
            f"admission {admission_id} for {package_id} is not current "
            f"(current revision: {current_hash or 'none'})"
        )


class GlyphRenderError(StudioError):
    """Base class for failures that must discard an incomplete glyph."""


class BudgetExceeded(GlyphRenderError):
    def __init__(self, budget_name: str, limit: int, used: int):
        self.budget_name = budget_name
        self.limit = limit
        self.used = used
        super().__init__(f"{budget_name} budget exceeded: {used} > {limit}")


class CanvasOverflow(GlyphRenderError):
    def __init__(self, x: int, y: int, width: int, height: int):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        super().__init__(
            f"square reaches ({x}, {y}) outside {width}x{height} virtual canvas"
        )


class CycleDetected(GlyphRenderError):
    def __init__(self, primitive_id: str, chain: tuple[str, ...] = ()):
        self.primitive_id = primitive_id
        self.chain = chain
        super().__init__(f"cyclic primitive reference at {primitive_id}: {' -> '.join(chain)}")


class LayoutOverflow(StudioError):
    """The locked profile has no feasible canvas area for the text."""
