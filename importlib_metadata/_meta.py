from __future__ import annotations

import os
from collections.abc import Iterator
from typing import (
    Any,
    Optional,
    Protocol,
    TypeVar,
    Union,
    overload,
)

from ._typing_compat import Self, TypeAlias


_StrPath: TypeAlias = Union[str, os.PathLike[str]]

_T = TypeVar("_T")


class PackageMetadata(Protocol):
    def __len__(self) -> int: ...

    def __contains__(self, name: str) -> bool: ...

    def __getitem__(self, name: str) -> str: ...

    def __iter__(self) -> Iterator[str]: ...

    @overload
    def get(self, name: str, failobj: None = None) -> Optional[str]: ...
    @overload
    def get(self, name: str, failobj: _T) -> Union[str, _T]: ...

    # overload per python/importlib_metadata#435
    @overload
    def get_all(self, name: str, failobj: None = None) -> Optional[list[Any]]: ...
    @overload
    def get_all(self, name: str, failobj: _T) -> Union[list[Any], _T]:
        """Return all values associated with a possibly multi-valued key."""
        ...

    @property
    def json(self) -> dict[str, Union[str, list[str]]]:
        """A JSON-compatible form of the metadata."""
        ...


class SimplePath(Protocol):
    """A minimal subset of pathlib.Path required by Distribution."""

    def joinpath(self, other: _StrPath) -> SimplePath: ...

    def __truediv__(self, other: _StrPath) -> SimplePath: ...

    @property
    def parent(self) -> Self: ...

    def read_text(self, encoding: Optional[str] = None) -> str: ...

    def read_bytes(self) -> bytes: ...

    def exists(self) -> bool: ...
