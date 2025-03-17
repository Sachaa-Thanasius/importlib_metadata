from __future__ import annotations

import pathlib

import importlib_metadata

from ._lazy_import import lazy_finder


with lazy_finder:
    import typing as _t

    from . import _meta


class FileHash:
    __slots__ = ("mode", "value")

    def __init__(self, spec: str) -> None:
        self.mode, _, self.value = spec.partition("=")

    def __repr__(self) -> str:
        return f"<FileHash mode: {self.mode} value: {self.value}>"


class PackagePath(pathlib.PurePosixPath):
    """A reference to a path in a package."""

    __slots__ = ("hash", "size", "dist")

    hash: _t.Optional[FileHash]
    size: _t.Optional[int]
    dist: importlib_metadata.Distribution

    def locate(self) -> _meta.SimplePath:
        """Return a path-like object for this path."""
        return self.dist.locate_file(self)

    def read_text(self, encoding: str = "utf-8") -> str:
        return self.locate().read_text(encoding=encoding)

    def read_binary(self) -> bytes:
        return self.locate().read_bytes()
