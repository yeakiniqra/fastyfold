"""fastyfold: a modern admin panel for FastAPI.

Usage reference for coding agents: llms.txt next to this file.
"""

from importlib import metadata as _metadata

from .admin import Admin
from .model import ModelAdmin, action

__all__ = ["Admin", "ModelAdmin", "action"]
try:
    __version__ = _metadata.version("fastyfold")
except _metadata.PackageNotFoundError:   # running from a source tree without installing
    __version__ = "0.0.0"
