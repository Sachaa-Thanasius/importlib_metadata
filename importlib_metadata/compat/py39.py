"""
Compatibility layer with Python 3.8/3.9
"""

from __future__ import annotations

import importlib_metadata as im

from .._lazy_import import lazy_finder


with lazy_finder:
    import typing as _t


def normalized_name(dist: im.Distribution) -> _t.Optional[str]:
    """
    Honor name normalization for distributions that don't provide ``_normalized_name``.
    """
    try:
        return dist._normalized_name
    except AttributeError:
        return im.Prepared.normalize(getattr(dist, "name", None) or dist.metadata["Name"])


def ep_matches(ep: im.EntryPoint, **params: _t.Any) -> bool:
    """
    Workaround for ``EntryPoint`` objects without the ``matches`` method.
    """
    try:
        return ep.matches(**params)
    except AttributeError:
        # Reconstruct the EntryPoint object to make sure it is compatible.
        return im.EntryPoint(ep.name, ep.value, ep.group).matches(**params)
