"""Local privacy-preserving collaborative subtitle editor."""

from .workspace import Workspace
from .merge import merge_documents
from .privacy import assert_clean, scan_for_leaks

__all__ = ["Workspace", "merge_documents", "scan_for_leaks", "assert_clean"]
__version__ = "1.0.0"
