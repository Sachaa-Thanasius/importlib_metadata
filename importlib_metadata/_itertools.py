from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from itertools import filterfalse

from . import _typing_compat as _tc
from ._lazy_import import lazy_finder


with lazy_finder:
    import typing as _t


def unique_everseen(iterable: Iterable[_tc.T], key: _t.Optional[Callable[[_tc.T], _tc.U]] = None) -> Iterator[_tc.T]:
    "List unique elements, preserving order. Remember all elements ever seen."
    # unique_everseen('AAAABBBCCDAABBB') --> A B C D
    # unique_everseen('ABBCcAD', str.lower) --> A B C D
    seen: set[_tc.T | _tc.U] = set()
    seen_add = seen.add
    if key is None:
        for element in filterfalse(seen.__contains__, iterable):
            seen_add(element)
            yield element
    else:
        for element in iterable:
            k = key(element)
            if k not in seen:
                seen_add(k)
                yield element
