from __future__ import annotations

import sys


TYPE_CHECKING = False

if TYPE_CHECKING:
    from types import GenericAlias
else:
    GenericAlias = type(list[int])


__all__ = (
    # typing
    "TypeAlias",
    "Self",
    # types
    "SimpleNamespace",
)


class _PlaceholderGenericAlias(GenericAlias):
    def __repr__(self, /):
        return f"<placeholder for {super().__repr__()}>"


class _PlaceholderMeta(type):
    _source_module: str  # pyright: ignore [reportUninitializedInstanceVariable]

    def __repr__(self, /) -> str:
        return f"<import placeholder for {self._source_module}.{self.__name__}>"


class _Placeholder(metaclass=_PlaceholderMeta):
    def __init_subclass__(cls, *args: object, source_module: str, generic: bool = False, **kwargs: object) -> None:
        super().__init_subclass__(*args, **kwargs)
        cls._source_module = source_module
        cls.__doc__ = f"Placeholder for {source_module}.{cls.__name__}."
        if generic:
            cls.__class_getitem__ = classmethod(_PlaceholderGenericAlias)  # pyright: ignore [reportUnknownMemberType]


if TYPE_CHECKING:
    from typing_extensions import TypeAlias
else:

    class TypeAlias(_Placeholder, source_module="typing"): ...


if TYPE_CHECKING:
    from typing_extensions import Self
else:

    class Self(_Placeholder, source_module="typing"): ...


if TYPE_CHECKING:
    from types import SimpleNamespace
else:
    SimpleNamespace = type(sys.implementation)


def __getattr__(name: str) -> object:
    if name == "C":
        global C  # noqa: PLW0603

        from typing import TypeVar

        C = TypeVar("C", bound=type)

        return C

    if name == "T":
        global T  # noqa: PLW0603

        from typing import TypeVar

        T = TypeVar("T")

        return T
    if name == "U":
        global U  # noqa: PLW0603

        from typing import TypeVar

        U = TypeVar("U")

        return U

    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


def __dir__() -> list[str]:
    return sorted(globals().keys() | {"C"})
