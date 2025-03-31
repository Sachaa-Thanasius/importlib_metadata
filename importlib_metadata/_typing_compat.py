from __future__ import annotations

import os  # noqa: F401 # Used in StrPath definition.

from . import _lazy_import


with _lazy_import.finder:
    import typing as _t  # noqa: F401 # Used in StrPath definition.


TYPE_CHECKING = False


__all__ = (
    "TYPE_CHECKING",
    "TypeAlias",
    "Self",
)


# PYUPDATE: py3.10 - Remove shim. Use _t.TypeAlias instead.
if TYPE_CHECKING:
    from typing_extensions import TypeAlias
else:

    class TypeAlias:
        """Placeholder for typing.TypeAlias."""


# PYUPDATE: py3.11 - Remove shim. Use _t.Self instead.
if TYPE_CHECKING:
    from typing_extensions import Self
else:

    class Self:
        """Placeholder for typing.Self."""


# Copied from typeshed.
StrPath: TypeAlias = "_t.Union[str, os.PathLike[str]]"
