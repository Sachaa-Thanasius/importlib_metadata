from __future__ import annotations

import _thread
import copy
import sys
from collections.abc import Sequence
from importlib.machinery import ModuleSpec, SourceFileLoader

from ._typing_compat import TYPE_CHECKING


# importlib.abc.Loader changed location in 3.10+ to become cheaper to import.
if TYPE_CHECKING:
    from importlib.abc import Loader
else:
    try:  # pragma: >=3.10 cover
        from importlib._abc import Loader
    except ImportError:  # pragma: <3.10 cover
        from importlib.abc import Loader


# types is an unnecessary import.
if TYPE_CHECKING:
    from types import ModuleType
else:
    ModuleType = type(sys)


# _find_spec takes care of finding module specs and uses importlib-specific thread locks.
# That makes it safer than manually importing through sys.meta_path and invoking find_spec for every finder.
if TYPE_CHECKING:

    def _find_spec(
        name: str,
        path: _t.Optional[Sequence[str]],
        target: _t.Optional[ModuleType],
    ) -> _t.Optional[ModuleSpec]: ...

else:
    from importlib._bootstrap import _find_spec


__all__ = ("lazy_finder",)


class LazyModuleType(ModuleType):
    """A subclass of the module type which triggers loading upon attribute access."""

    def __getattribute__(self, name: str, /) -> _t.Any:
        """Trigger the load of the module and return the attribute."""

        __spec__: ModuleSpec = object.__getattribute__(self, "__spec__")

        # We want to avoid the importlib machinery accidentally causing a load
        # when it checks a lazy module in sys.modules to see if it is initialized.
        # Since the machinery uses module.__spec__ to perform that check, return that without loading.
        #
        # This does mean a user can get __spec__ from a lazy module and modify it without causing a load. However:
        #
        # 1. For our use case, this should be good enough.
        # 2. If a user does that, they'd modify a *copy* of the spec, not the actual spec,
        #    and the copy would only replace the actual as module.__spec__ when the load occurs.
        #    See `LazyLoader.exec_module()`.
        if name == "__spec__":
            return __spec__

        loader_state = __spec__.loader_state
        with loader_state["lock"]:
            # Only the first thread to get the lock should trigger the load
            # and reset the module's class. The rest can now getattr().
            if object.__getattribute__(self, "__class__") is LazyModuleType:
                __class__ = loader_state["__class__"]

                # Reentrant calls from the same thread must be allowed to proceed without
                # triggering the load again.
                # exec_module() and self-referential imports are the primary ways this can
                # happen, but in any case we must return something to avoid deadlock.
                if loader_state["is_loading"]:
                    return __class__.__getattribute__(self, name)
                loader_state["is_loading"] = True

                __dict__: dict[str, _t.Any] = __class__.__getattribute__(self, "__dict__")

                # All module metadata must be gathered from __spec__ in order to avoid
                # using mutated values.
                # Get the original name to make sure no object substitution occurred
                # in sys.modules.
                original_name = __spec__.name

                # Figure out exactly what attributes were mutated between the creation
                # of the module and now.
                attrs_then: dict[str, _t.Any] = loader_state["__dict__"]
                attrs_now = __dict__
                attrs_updated = {
                    key: value
                    for key, value in attrs_now.items()
                    # Code that set an attribute may have kept a reference to the
                    # assigned object, making identity more important than equality.
                    if (key not in attrs_then) or (attrs_now[key] is not attrs_then[key])
                }

                assert __spec__.loader is not None, "This spec must have an actual loader."
                __spec__.loader.exec_module(self)

                # If exec_module() was used directly there is no guarantee the module
                # object was put into sys.modules.
                if (original_name in sys.modules) and (self is not sys.modules[original_name]):
                    msg = f"module object for {original_name!r} substituted in sys.modules during a lazy load"
                    raise ValueError(msg)

                # Update after loading since that's what would happen in an eager
                # loading situation.
                __dict__ |= attrs_updated

                # Finally, stop triggering this method, if the module did not
                # already update its own __class__.
                if isinstance(self, LazyModuleType):
                    object.__setattr__(self, "__class__", __class__)

        return getattr(self, name)

    def __delattr__(self, name: str, /) -> None:
        """Trigger the load and then perform the deletion."""

        # To trigger the load and raise an exception if the attribute
        # doesn't exist.
        self.__getattribute__(name)
        delattr(self, name)


class LazyLoader(Loader):
    """A loader that creates a module which defers loading until attribute access."""

    def __init__(self, loader: Loader) -> None:
        if not hasattr(loader, "exec_module"):
            msg = "loader must define exec_module()"
            raise TypeError(msg)

        self.loader = loader

    def create_module(self, spec: ModuleSpec) -> _t.Optional[ModuleType]:
        return self.loader.create_module(spec)

    def exec_module(self, module: ModuleType) -> None:
        """Make the module load lazily."""

        assert module.__spec__, "The module should have been initialized with a spec."

        module.__spec__.loader = self.loader
        module.__loader__ = self.loader

        # Deep-copy module.__spec__ so that accessing/modifying it won't trigger a load.
        # For everything else, though...
        # Don't need to worry about deep-copying as trying to set an attribute
        # on an object would have triggered the load,
        # e.g. ``module.__loader__.create_module = None`` would trigger a load from
        # trying to access module.__loader__.
        loader_state = {
            "__dict__": module.__dict__.copy() | {"__spec__": copy.deepcopy(module.__spec__)},
            "__class__": module.__class__,
            "lock": _thread.RLock(),
            "is_loading": False,
        }
        module.__spec__.loader_state = loader_state
        module.__class__ = LazyModuleType


class LazyFinder:
    """A finder that wraps loaders for source modules with `LazyLoader`."""

    @classmethod
    def find_spec(
        cls,
        name: str,
        path: _t.Optional[Sequence[str]] = None,
        target: _t.Optional[ModuleType] = None,
    ) -> _t.Optional[ModuleSpec]:
        sys.meta_path.remove(cls)

        try:
            spec = _find_spec(name, path, target)

            # Skip being lazy for non-source modules to avoid issues with extension modules having
            # uninitialized state, especially when loading can't currently be triggered by PyModule_GetState.
            # Ref: https://github.com/python/cpython/issues/85963
            if (spec is not None) and isinstance(spec.loader, SourceFileLoader):
                spec.loader = LazyLoader(spec.loader)

            return spec
        finally:
            sys.meta_path.insert(0, cls)


class LazyFinderContext:
    """A context manager that temporarily lazifies contained imports (if the modules are written in Python)."""

    __slots__ = ()

    def __enter__(self, /) -> None:
        sys.meta_path.insert(0, LazyFinder)

    def __exit__(self, *_dont_care: object) -> None:
        try:
            sys.meta_path.remove(LazyFinder)
        except ValueError:
            # XXX: Maybe raise a warning here?
            pass


lazy_finder = LazyFinderContext()

with lazy_finder:
    import typing as _t
