"""INTERNAL.

A reexport shim/middleman for typing-related symbols, annotation-related symbols, and modules to avoid import-time
dependencies on expensive modules (like `typing` and `pathlib`) or third-party imports (like `typing-extensions`).
Some of the symbols or modules may eventually be needed at runtime, but their import/creation will be "on demand"
to improve startup performance.

Usage Notes
-----------
Do not directly import annotation-related symbols from this module (e.g. ``from ._lazy import Any``)!
Doing so will trigger the module-level `__getattr__`, causing shimmed modules, e.g. `typing`, to get imported.
Instead, import the module and use symbols via attribute access as needed (e.g. ``from . import _lazy [as _t]``).

Additionally, to avoid those symbols being evaluated at runtime, which would _also_ cause shimmed modules to get imported,
make sure to defer evaluation of annotations via the following:

    a) <3.14: Manual stringification of annotations, or `from __future__ import annotations`.
    b) >=3.14: Nothing, thanks to default PEP 649 semantics.
"""

from __future__ import annotations

import sys

TYPE_CHECKING = False

__all__ = (
    # ---- Modules ----
    # sibling
    "_meta",

    # ---- Typing/annotation symbols ----
    # typing
    "Any",
    "NoReturn",
    "Optional",
    "Union",
    "Self",  # >=3.11

    # types
    "SimpleNamespace",

    # Other
    "TypeT",

    # ---- Used at runtime ----
    "TYPE_CHECKING",

)  # fmt: skip


# Type checkers (well, mypy) needs this block to understand what __getattr__() does currently.
if TYPE_CHECKING:
    from typing import Any, NoReturn, Optional, TypeVar, Union

    from . import _meta

    TypeT = TypeVar("TypeT", bound=type)


def __getattr__(name: str) -> object:
    if name == "_meta":
        from . import _meta as obj

    if name in {"Any", "NoReturn", "Optional", "Union"} or (
        sys.version_info >= (3, 11) and name == "Self"
    ):
        import typing

        obj = getattr(typing, name)

    elif name == "TypeT":
        from typing import TypeVar

        obj = TypeVar("TypeT", bound=type)

    else:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)

    globals()[name] = obj
    return obj


def __dir__() -> list[str]:
    return sorted(globals().keys() | __all__)


# No need to import types.
if TYPE_CHECKING:
    from types import SimpleNamespace
else:
    SimpleNamespace = type(sys.implementation)


if TYPE_CHECKING:
    from typing_extensions import Self
elif sys.version_info < (3, 11):

    class Self:
        """Placeholder for typing.Self."""
