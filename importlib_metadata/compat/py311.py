import os
import sys

from .._lazy_import import lazy_finder
from .._typing_compat import SimpleNamespace


with lazy_finder:
    import pathlib


def wrap(path):  # pragma: no cover
    """
    Workaround for https://github.com/python/cpython/issues/84538
    to add backward compatibility for walk_up=True.
    An example affected package is dask-labextension, which uses
    jupyter-packaging to install JupyterLab javascript files outside
    of site-packages.
    """

    def relative_to(root, *, walk_up: bool = False) -> pathlib.Path:
        return pathlib.Path(os.path.relpath(path, root))

    return SimpleNamespace(relative_to=relative_to)


relative_fix = wrap if sys.version_info < (3, 12) else lambda x: x
