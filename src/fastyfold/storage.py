"""Uploaded files. `Admin(media_dir=...)` keeps them on disk; pass `Admin(storage=...)` for anything else.

A storage is any object with:

    async def save(self, name: str, data: bytes, content_type: str | None) -> str   # returns the key to store
    async def delete(self, key: str) -> None
    def url(self, key: str) -> str      # optional; without it the admin serves the file at /_media/<key>
"""

import re
from pathlib import Path

import anyio

from .fields import FieldError

IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_IMAGE_MAGIC = {".png": (b"\x89PNG\r\n\x1a\n",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
                ".gif": (b"GIF87a", b"GIF89a"), ".webp": (b"RIFF",)}


def extension(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    return ext if re.fullmatch(r"\.[a-z0-9]{1,10}", ext) else ""


def check_upload(kind: str, filename: str, data: bytes, max_size: int) -> None:
    """Raise FieldError unless `data` is a non-empty file within the size limit (and a real image for kind "image")."""
    if not data:
        raise FieldError("The file is empty.")
    if len(data) > max_size:
        raise FieldError(f"Files can be up to {_size(max_size)}.")
    if kind == "image":
        ext = extension(filename)
        ok = ext in _IMAGE_MAGIC and data.startswith(_IMAGE_MAGIC[ext]) and (ext != ".webp" or data[8:12] == b"WEBP")
        if not ok:
            # SVG is left out on purpose: it can carry script.
            raise FieldError("Upload a PNG, JPEG, GIF or WebP image.")


def _size(n: int) -> str:
    return f"{n // (1024 * 1024)} MB" if n >= 1024 * 1024 else f"{max(1, n // 1024)} KB"


class LocalStorage:
    def __init__(self, root):
        self.root = Path(root).resolve()

    async def save(self, name: str, data: bytes, content_type: str | None = None) -> str:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        await anyio.to_thread.run_sync(path.write_bytes, data)
        return name

    async def delete(self, key: str) -> None:
        if path := self.path(key):
            path.unlink(missing_ok=True)

    def path(self, key: str) -> Path | None:
        """The file for `key`, or None if it is missing or outside the media directory."""
        path = (self.root / key).resolve()
        return path if path.is_relative_to(self.root) and path.is_file() else None
