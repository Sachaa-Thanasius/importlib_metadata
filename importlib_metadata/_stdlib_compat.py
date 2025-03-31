from __future__ import annotations

import sys


TYPE_CHECKING = False

if TYPE_CHECKING:
    from typing import TypeVar as _TypeVar

    C = _TypeVar("C", bound=type)
else:
    C = type


__all__ = ("install",)


def install(cls: C) -> C:
    """Class decorator for installation on sys.meta_path.

    Adds the backport DistributionFinder to sys.meta_path and
    attempts to disable the finder functionality of the stdlib
    DistributionFinder.
    """
    sys.meta_path.append(cls())
    disable_stdlib_finder()
    return cls


def disable_stdlib_finder() -> None:
    """Give the backport primacy for discovering path-based distributions
    by monkey-patching the stdlib O_O.

    See #91 for more background for rationale on this sketchy
    behavior.
    """

    for finder in sys.meta_path:
        if (
            getattr(finder, "__module__", None) == "_frozen_importlib_external"
            and hasattr(finder, "find_distributions")
        ):  # fmt: skip
            del finder.find_distributions  # pyright: ignore [reportAttributeAccessIssue]
