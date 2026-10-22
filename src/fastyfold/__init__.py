"""fastyfold: a modern admin panel for FastAPI."""

from .admin import Admin
from .model import ModelAdmin, action

__all__ = ["Admin", "ModelAdmin", "action"]
__version__ = "0.1.0"
