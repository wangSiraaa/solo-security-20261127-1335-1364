"""Restricted declarative grid-font importer and safe subtitle compositor."""

from .errors import (
    BudgetExceeded,
    CanvasOverflow,
    CycleDetected,
    PackageFormatError,
    PackageQuotaError,
    StaleAdmissionError,
    StudioError,
)
from .studio import FontStudio

# Descriptive aliases for integrators looking for an importer/compositor.
FontImporter = FontStudio
SubtitleCompositor = FontStudio
from .profiles import LayoutProfile, MachinePolicy

__all__ = [
    "FontStudio",
    "FontImporter",
    "SubtitleCompositor",
    "LayoutProfile",
    "MachinePolicy",
    "StudioError",
    "PackageFormatError",
    "PackageQuotaError",
    "StaleAdmissionError",
    "BudgetExceeded",
    "CanvasOverflow",
    "CycleDetected",
]

__version__ = "1.0.0"
