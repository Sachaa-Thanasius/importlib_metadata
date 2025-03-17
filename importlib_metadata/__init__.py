"""APIs exposing metadata from third-party Python packages.

This codebase is shared between importlib.metadata in the stdlib
and importlib_metadata in PyPI. See
https://github.com/python/importlib_metadata/wiki/Development-Methodology
for more detail.
"""

from __future__ import annotations

import abc
import email
import functools
import importlib
import importlib.machinery
import os
import posixpath
import re
import sys
from collections import defaultdict
from collections.abc import Callable, Generator, Iterable, Mapping
from itertools import chain, filterfalse, tee

from ._lazy_import import lazy_finder
from ._stdlib_compat import install
from ._typing_compat import TYPE_CHECKING, Self, SimpleNamespace, TypeAlias
from .compat import py39, py311


with lazy_finder:
    import json
    import pathlib
    import typing as _t

    from . import _adapters, _meta, _path


# Copied from typeshed
_StrPath: TypeAlias = "_t.Union[str, os.PathLike[str]]"


__all__ = (
    "Distribution",
    "DistributionFinder",
    "PackageMetadata",
    "PackageNotFoundError",
    "SimplePath",
    "distribution",
    "distributions",
    "entry_points",
    "files",
    "metadata",
    "packages_distributions",
    "requires",
    "version",
)


def __getattr__(name: str, /) -> _t.Any:
    # Lazily import and assign these names.
    if name in {"PackageMetadata", "SimplePath"}:
        global PackageMetadata, SimplePath  # noqa: PLW0603

        from ._meta import PackageMetadata, SimplePath

        return globals()[name]

    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


def __dir__() -> list[str]:
    return sorted(globals().keys() | {"PackageMetadata", "SimplePath"})


class PackageNotFoundError(ModuleNotFoundError):
    """The package was not found."""

    def __init__(self, name: str) -> None:
        super().__init__(f"No package metadata was found for {name}", name=name)


class Pair:
    __slots__ = ("name", "value")

    def __init__(self, name: str, value: _t.Any) -> None:
        self.name: str = name
        self.value: _t.Any = value

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, value={self.value!r})"

    def _replace(self, **kwargs: _t.Any) -> Self:
        new_values = {name: getattr(self, name) for name in self.__slots__} | kwargs
        return self.__class__(**new_values)

    @classmethod
    def parse(cls, text: str) -> Self:
        return cls(*map(str.strip, text.split("=", 1)))


class Sectioned:
    """A simple entry point config parser for performance.

    >>> sample = '''
    ... [sec1]
    ... # comments ignored
    ... a = 1
    ... b = 2
    ...
    ... [sec2]
    ... a = 2
    ... '''
    >>> for item in Sectioned.read(sample):
    ...     print(item)
    Pair(name='sec1', value='# comments ignored')
    Pair(name='sec1', value='a = 1')
    Pair(name='sec1', value='b = 2')
    Pair(name='sec2', value='a = 2')

    >>> res = Sectioned.section_pairs(sample)
    >>> item = next(res)
    >>> item.name
    'sec1'
    >>> item.value
    Pair(name='a', value='1')
    >>> item = next(res)
    >>> item.value
    Pair(name='b', value='2')
    >>> item = next(res)
    >>> item.name
    'sec2'
    >>> item.value
    Pair(name='a', value='2')
    >>> list(res)
    []
    """

    @classmethod
    def section_pairs(cls, text: str) -> Generator[Pair]:
        for section in cls.read(text, filter_=cls.valid):
            if section.name is not None:
                yield section._replace(value=Pair.parse(section.value))

    @staticmethod
    def read(text: str, filter_: _t.Optional[Callable[[str], object]] = None) -> Generator[Pair]:
        lines = filter(filter_, map(str.strip, text.splitlines()))
        name = None
        for value in lines:
            section_match = value.startswith("[") and value.endswith("]")
            if section_match:
                name = value.strip("[]")
                continue
            yield Pair(name, value)

    @staticmethod
    def valid(line: str) -> bool:
        return bool(line and not line.startswith("#"))


class EntryPoint:
    """An entry point as defined by Python packaging conventions.

    See `the packaging docs on entry points
    <https://packaging.python.org/specifications/entry-points/>`_
    for more information.

    >>> ep = EntryPoint(
    ...     name=None, group=None, value='package.module:attr [extra1, extra2]')
    >>> ep.module
    'package.module'
    >>> ep.attr
    'attr'
    >>> ep.extras
    ['extra1', 'extra2']
    """

    pattern = re.compile(
        r"(?P<module>[\w.]+)\s*"
        r"(:\s*(?P<attr>[\w.]+)\s*)?"
        r"((?P<extras>\[.*\])\s*)?$"
    )
    """
    A regular expression describing the syntax for an entry point,
    which might look like:

        - module
        - package.module
        - package.module:attribute
        - package.module:object.attribute
        - package.module:attr [extra1, extra2]

    Other combinations are possible as well.

    The expression is lenient about whitespace around the ':',
    following the attr, and following any extras.
    """

    __slots__ = ("name", "value", "group", "dist")

    name: str
    value: str
    group: str
    dist: _t.Optional[Distribution]

    def __init__(self, name: str, value: str, group: str) -> None:
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "group", group)
        object.__setattr__(self, "dist", None)

    def load(self) -> _t.Any:
        """Load the entry point from its definition. If only a module
        is indicated by the value, return that module. Otherwise,
        return the named object.
        """
        match = self.pattern.match(self.value)
        assert match is not None
        module = importlib.import_module(match.group("module"))
        attrs = filter(None, (match.group("attr") or "").split("."))
        return functools.reduce(getattr, attrs, module)

    @property
    def module(self) -> str:
        match = self.pattern.match(self.value)
        assert match is not None
        return match.group("module")

    @property
    def attr(self) -> str:
        match = self.pattern.match(self.value)
        assert match is not None
        return match.group("attr")

    @property
    def extras(self) -> list[str]:
        match = self.pattern.match(self.value)
        assert match is not None
        return re.findall(r"\w+", match.group("extras") or "")

    def _for_dist(self, dist: Distribution) -> Self:
        object.__setattr__(self, "dist", dist)
        return self

    def matches(self, **params: _t.Any) -> bool:
        """Determine if this entry point matches the given parameters.

        >>> ep = EntryPoint(group='foo', name='bar', value='bing:bong [extra1, extra2]')
        >>> ep.matches(group='foo')
        True
        >>> ep.matches(name='bar', value='bing:bong [extra1, extra2]')
        True
        >>> ep.matches(group='foo', name='other')
        False
        >>> ep.matches()
        True
        >>> ep.matches(extras=['extra1', 'extra2'])
        True
        >>> ep.matches(module='bing')
        True
        >>> ep.matches(attr='bong')
        True
        """
        self._disallow_dist(params)
        attrs = (getattr(self, param) for param in params)
        return all(param_val == attr for param_val, attr in zip(params.values(), attrs))

    @staticmethod
    def _disallow_dist(params: dict[str, _t.Any]) -> None:
        """Querying by dist is not allowed (dist objects are not comparable).

        >>> EntryPoint(name='fan', value='fav', group='fag').matches(dist='foo')
        Traceback (most recent call last):
        ...
        ValueError: "dist" is not suitable for matching...
        """
        if "dist" in params:
            msg = (
                '"dist" is not suitable for matching. '
                "Instead, use Distribution.entry_points.select() on a "
                "located distribution."
            )
            raise ValueError(msg)

    def _key(self) -> tuple[str, str, str]:
        return (self.name, self.value, self.group)

    def __repr__(self):
        return f"{self.__class__.__name__}(name={self.name!r}, value={self.value!r}, group={self.group!r})"

    def __hash__(self) -> int:
        return hash(self._key())

    def __eq__(self, other: object, /) -> bool:
        if not isinstance(other, type(self)):
            return NotImplemented
        return self._key() == other._key()

    def __lt__(self, other: Self, /) -> bool:
        if not isinstance(other, type(self)):
            return NotImplemented
        return self._key() < other._key()

    def __setattr__(self, name: str, value: _t.Any, /):
        msg = "EntryPoint objects are immutable."
        raise AttributeError(msg)

    def __getstate__(self):
        return (self.name, self.value, self.group, self.dist)

    def __setstate__(self, state: tuple[_t.Any, ...]) -> None:
        object.__setattr__(self, "name", state[0])
        object.__setattr__(self, "value", state[1])
        object.__setattr__(self, "group", state[2])
        object.__setattr__(self, "dist", state[3])


class EntryPoints(tuple[EntryPoint, ...]):
    """An immutable collection of selectable EntryPoint objects."""

    __slots__ = ()

    @property
    def names(self) -> set[str]:
        """The set of all names of all entry points."""

        return {ep.name for ep in self}

    @property
    def groups(self) -> set[str]:
        """The set of all groups of all entry points."""

        return {ep.group for ep in self}

    def __repr__(self):
        """Repr with classname and tuple constructor to signal that we deviate from regular tuple behavior."""

        return f"{self.__class__.__name__}({tuple(self)!r})"

    def __getitem__(self, name: str, /) -> EntryPoint:  # type: ignore[override] # Work with str instead of int
        """Get the EntryPoint in self matching name."""

        try:
            return next(iter(self.select(name=name)))
        except StopIteration:
            raise KeyError(name) from None

    def select(self, **params: _t.Any) -> EntryPoints:
        """Select entry points from self that match the given parameters (typically group and/or name)."""

        return EntryPoints(ep for ep in self if py39.ep_matches(ep, **params))


class Distribution(metaclass=abc.ABCMeta):
    """
    An abstract Python distribution package.

    Custom providers may derive from this class and define
    the abstract methods to provide a concrete implementation
    for their environment. Some providers may opt to override
    the default implementation of some properties to bypass
    the file-reading mechanism.
    """

    @abc.abstractmethod
    def read_text(self, filename: str) -> _t.Optional[str]:
        """Attempt to load metadata file given by the name.

        Python distribution metadata is organized by blobs of text
        typically represented as "files" in the metadata directory
        (e.g. package-1.0.dist-info). These files include things
        like:

        - METADATA: The distribution metadata including fields
          like Name and Version and Description.
        - entry_points.txt: A series of entry points as defined in
          `the entry points spec <https://packaging.python.org/en/latest/specifications/entry-points/#file-format>`_.
        - RECORD: A record of files according to
          `this recording spec <https://packaging.python.org/en/latest/specifications/recording-installed-packages/#the-record-file>`_.

        A package may provide any set of files, including those
        not listed here or none at all.

        :param filename: The name of the file in the distribution info.
        :return: The text if found, otherwise None.
        """

    @abc.abstractmethod
    def locate_file(self, path: _StrPath) -> _meta.SimplePath:
        """
        Given a path to a file in this distribution, return a SimplePath
        to it.

        This method is used by callers of ``Distribution.files()`` to
        locate files within the distribution. If it's possible for a
        Distribution to represent files in the distribution as
        ``SimplePath`` objects, it should implement this method
        to resolve such objects.

        Some Distribution providers may elect not to resolve SimplePath
        objects within the distribution by raising a
        NotImplementedError, but consumers of such a Distribution would
        be unable to invoke ``Distribution.files()``.
        """

    @classmethod
    def from_name(cls, name: str) -> Distribution:
        """Return the Distribution for the given package name.

        :param name: The name of the distribution package to search for.
        :return: The Distribution instance (or subclass thereof) for the named
            package, if found.
        :raises PackageNotFoundError: When the named package's distribution
            metadata cannot be found.
        :raises ValueError: When an invalid value is supplied for name.
        """
        if not name:
            msg = "A distribution name is required."
            raise ValueError(msg)
        try:
            return next(iter(cls._prefer_valid(cls.discover(name=name))))
        except StopIteration:
            raise PackageNotFoundError(name) from None

    @classmethod
    def discover(
        cls,
        *,
        context: _t.Optional[DistributionFinder.Context] = None,
        **kwargs: _t.Any,
    ) -> Iterable[Distribution]:
        """Return an iterable of Distribution objects for all packages.

        Pass a ``context`` or pass keyword arguments for constructing
        a context.

        :context: A ``DistributionFinder.Context`` object.
        :return: Iterable of Distribution objects for packages matching
          the context.
        """
        if context and kwargs:
            msg = "cannot accept context and kwargs"
            raise ValueError(msg)

        context = context or DistributionFinder.Context(**kwargs)
        for resolver in cls._discover_resolvers():
            yield from resolver(context)

    @staticmethod
    def _prefer_valid(dists: Iterable[Distribution]) -> Iterable[Distribution]:
        """
        Prefer (move to the front) distributions that have metadata.

        Ref python/importlib_resources#489.
        """

        # NOTE: Implementation based on partition() in itertools recipes.

        def has_metadata(dist: Distribution) -> bool:
            return bool(dist.metadata)

        dists1, dists2 = tee(dists)
        return chain(filter(has_metadata, dists1), filterfalse(has_metadata, dists2))

    @staticmethod
    def at(path: _StrPath) -> Distribution:
        """Return a Distribution for the indicated metadata path.

        :param path: a string or path-like object
        :return: a concrete Distribution instance for the path
        """
        return PathDistribution(pathlib.Path(path))

    @staticmethod
    def _discover_resolvers() -> Iterable[Callable[[DistributionFinder.Context], Iterable[Distribution]]]:
        """Search the meta_path for resolvers (MetadataPathFinders)."""
        declared = (getattr(finder, "find_distributions", None) for finder in sys.meta_path)
        return filter(None, declared)

    @property
    def metadata(self) -> _meta.PackageMetadata:
        """Return the parsed metadata for this Distribution.

        The returned object will have keys that name the various bits of
        metadata per the
        `Core metadata specifications <https://packaging.python.org/en/latest/specifications/core-metadata/#core-metadata>`_.

        Custom providers may provide the METADATA file or override this
        property.
        """

        opt_text = (
            self.read_text("METADATA")
            or self.read_text("PKG-INFO")
            # This last clause is here to support old egg-info files.  Its
            # effect is to just end up using the PathDistribution's self._path
            # (which points to the egg-info file) attribute unchanged.
            or self.read_text("")
        )
        return _adapters.NaturalMessage.from_original(email.message_from_string(opt_text))

    @property
    def name(self) -> str:
        """`str`: The 'Name' metadata for the distribution package."""

        return self.metadata["Name"]

    @property
    def _normalized_name(self) -> str:
        """`str`: A normalized version of the name."""

        return Prepared.normalize(self.name)

    @property
    def version(self) -> str:
        """`str`: The 'Version' metadata for the distribution package."""

        return self.metadata["Version"]

    @property
    def entry_points(self) -> EntryPoints:
        """`EntryPoints`: The entry points for this distribution.

        Custom providers may provide the ``entry_points.txt`` file or override this property.
        """

        return EntryPoints(
            EntryPoint(name=item.value.name, value=item.value.value, group=item.name)._for_dist(self)
            for item in Sectioned.section_pairs(self.read_text("entry_points.txt") or "")
        )

    @property
    def files(self) -> _t.Optional[list[_path.PackagePath]]:
        """Files in this distribution.

        :return: List of PackagePath for this distribution or None

        Result is `None` if the metadata file that enumerates files
        (i.e. RECORD for dist-info, or installed-files.txt or
        SOURCES.txt for egg-info) is missing.
        Result may be empty if the metadata exists but is empty.

        Custom providers are recommended to provide a "RECORD" file (in
        ``read_text``) or override this property to allow for callers to be
        able to resolve filenames provided by the package.
        """

        def make_file(name: str, hash: _t.Optional[str] = None, size_str: _t.Optional[str] = None) -> _path.PackagePath:  # noqa: A002
            result = _path.PackagePath(name)
            result.hash = _path.FileHash(hash) if hash else None
            result.size = int(size_str) if size_str else None
            result.dist = self
            return result

        def make_files(lines: _t.Optional[list[str]]) -> Generator[_path.PackagePath]:
            if lines is None:
                return None

            # Delay csv import, since Distribution.files is not as widely used
            # as other parts of importlib.metadata
            import csv

            for row in csv.reader(lines):
                yield make_file(*row)

        def skip_missing_files(
            package_paths: _t.Optional[Iterable[_path.PackagePath]],
        ) -> _t.Optional[list[_path.PackagePath]]:
            if package_paths is None:
                return None
            return [path for path in package_paths if path.locate().exists()]

        return skip_missing_files(
            make_files(
                self._read_files_distinfo()
                or self._read_files_egginfo_installed()
                or self._read_files_egginfo_sources()
            )
        )

    def _read_files_distinfo(self) -> _t.Optional[list[str]]:
        """Read the lines of RECORD."""

        text = self.read_text("RECORD")
        return text.splitlines() if (text is not None) else None

    def _read_files_egginfo_installed(self) -> _t.Optional[list[str]]:
        """Read installed-files.txt and return lines in a similar
        CSV-parsable format as RECORD: each file must be placed
        relative to the site-packages directory and must also be
        quoted (since file names can contain literal commas).

        This file is written when the package is installed by pip,
        but it might not be written for other installation methods.
        Assume the file is accurate if it exists.
        """
        text = self.read_text("installed-files.txt")
        # Prepend the .egg-info/ subdir to the lines in this file.
        # But this subdir is only available from PathDistribution's
        # self._path.
        subdir = getattr(self, "_path", None)
        if not text or not subdir:
            return None

        paths = (
            py311.relative_fix((subdir / name).resolve())
            .relative_to(self.locate_file("").resolve(), walk_up=True)
            .as_posix()
            for name in text.splitlines()
        )
        return [f'"{path}"' for path in paths]

    def _read_files_egginfo_sources(self) -> _t.Optional[list[str]]:
        """Read SOURCES.txt and return lines in a similar CSV-parsable
        format as RECORD: each file name must be quoted (since it
        might contain literal commas).

        Note that SOURCES.txt is not a reliable source for what
        files are installed by a package. This file is generated
        for a source archive, and the files that are present
        there (e.g. setup.py) may not correctly reflect the files
        that are present after the package has been installed.
        """
        text = self.read_text("SOURCES.txt")
        return [f'"{line}"' for line in text.splitlines()] if (text is not None) else None

    @property
    def requires(self) -> _t.Optional[list[str]]:
        """Generated requirements specified for this Distribution"""
        reqs = self._read_dist_info_reqs() or self._read_egg_info_reqs()
        return reqs and list(reqs)

    def _read_dist_info_reqs(self):
        return self.metadata.get_all("Requires-Dist")

    def _read_egg_info_reqs(self):
        source = self.read_text("requires.txt")
        return (self._deps_from_requires_text(source)) if (source is not None) else None

    @classmethod
    def _deps_from_requires_text(cls, source: str) -> Generator[str]:
        return cls._convert_egg_info_reqs_to_simple_reqs(Sectioned.read(source))

    @staticmethod
    def _convert_egg_info_reqs_to_simple_reqs(sections: Iterable[Pair]) -> Generator[str]:
        """
        Historically, setuptools would solicit and store 'extra'
        requirements, including those with environment markers,
        in separate sections. More modern tools expect each
        dependency to be defined separately, with any relevant
        extras and environment markers attached directly to that
        requirement. This method converts the former to the
        latter. See _test_deps_from_requires_text for an example.
        """

        def url_req_space(req: str) -> str:
            """PEP 508 requires a space between the url_spec and the quoted_marker.
            Ref python/importlib_metadata#357.
            """
            # '@' is uniquely indicative of a url_req.
            return " " * ("@" in req)

        for section in sections:
            space = url_req_space(section.value)

            section_name = section.name or ""
            extra, _sep, markers = section_name.partition(":")
            conditions: list[str] = []

            # Format the conditions as needed if they exist.
            if extra and markers:
                markers = f"({markers})"
            if extra:
                extra = f'extra == "{extra}"'

            # Add them to the conditions if they exist.
            if markers:
                conditions.append(markers)
            if extra:
                conditions.append(extra)

            # Assemble the marker if there are any conditions.
            quoted_marker = ("; " + " and ".join(conditions)) if conditions else ""

            yield section.value + space + quoted_marker

    @property
    def origin(self) -> _t.Any:
        return self._load_json("direct_url.json")

    def _load_json(self, filename: str) -> _t.Any:
        text = self.read_text(filename)
        if text is None:
            return None
        return json.loads(text, object_hook=lambda data: SimpleNamespace(**data))


class DistributionFinder:
    """A MetaPathFinder capable of discovering installed distributions.

    Custom providers should implement this interface in order to
    supply metadata.
    """

    class Context:
        """
        Keyword arguments presented by the caller to
        ``distributions()`` or ``Distribution.discover()``
        to narrow the scope of a search for distributions
        in all DistributionFinders.

        Each DistributionFinder may expect any parameters
        and should attempt to honor the canonical
        parameters defined below when appropriate.

        This mechanism gives a custom provider a means to
        solicit additional details from the caller beyond
        "name" and "path" when searching distributions.
        For example, imagine a provider that exposes suites
        of packages in either a "public" or "private" ``realm``.
        A caller may wish to query only for distributions in
        a particular realm and could call
        ``distributions(realm="private")`` to signal to the
        custom provider to only include distributions from that
        realm.
        """

        name = None
        """
        Specific name for which a distribution finder should match.
        A name of ``None`` matches all distributions.
        """

        def __init__(self, **kwargs: _t.Any) -> None:
            self.__dict__ |= kwargs

        @property
        def path(self) -> list[str]:
            """The sequence of directory path that a distribution finder
            should search.

            Typically refers to Python installed package paths such as
            "site-packages" directories and defaults to ``sys.path``.
            """
            return vars(self).get("path", sys.path)

    @abc.abstractmethod
    def find_distributions(self, context: Context = Context()) -> Iterable[Distribution]:
        """
        Find distributions.

        Return an iterable of all Distribution instances capable of
        loading the metadata for packages matching the ``context``,
        a DistributionFinder.Context instance.
        """


class FastPath:
    """
    Micro-optimized class for searching a root for children.

    Root is a path on the file system that may contain metadata
    directories either as natural directories or within a zip file.

    >>> FastPath('').children()
    ['...']

    FastPath objects are cached and recycled for any given root.

    >>> FastPath('foobar') is FastPath('foobar')
    True
    """

    root: str
    lookup: Callable[[float], Lookup]

    @functools.lru_cache
    def __new__(cls, root: str):
        self = super().__new__(cls)
        self.root = root
        self.lookup = functools.lru_cache(self._lookup_uncached)
        return self

    @property
    def mtime(self) -> _t.Optional[float]:
        try:
            return os.stat(self.root).st_mtime
        except OSError:
            pass

        self.lookup.cache_clear()
        return None

    def _lookup_uncached(self, mtime: float) -> Lookup:
        return Lookup(self)

    def joinpath(self, child: str) -> pathlib.Path:
        return pathlib.Path(self.root, child)

    def children(self) -> list[str]:
        try:
            return os.listdir(self.root or ".")
        except Exception:
            pass

        try:
            return self.zip_children()
        except Exception:
            pass

        return []

    def zip_children(self) -> list[str]:
        # deferred for performance (python/importlib_metadata#502)
        if TYPE_CHECKING:
            import zipfile
        else:
            from zipp.compat.overlay import zipfile

        zip_path = zipfile.Path(self.root)
        names = zip_path.root.namelist()
        self.joinpath = zip_path.joinpath

        return list(dict.fromkeys(child.split(posixpath.sep, 1)[0] for child in names))

    def search(self, name: Prepared) -> Iterable[pathlib.Path]:
        return self.lookup(self.mtime).search(name)


class Lookup:
    """A micro-optimized class for searching a (fast) path for metadata."""

    __slots__ = ("infos", "eggs")

    def __init__(self, path: FastPath):
        """Calculate all of the children representing metadata.

        From the children in the path, calculate early all of the
        children that appear to represent metadata (infos) or legacy
        metadata (eggs).
        """

        base = os.path.basename(path.root).lower()
        base_is_egg = base.endswith(".egg")
        self.infos: defaultdict[str, list[pathlib.Path]] = defaultdict(list)
        self.eggs: defaultdict[str, list[pathlib.Path]] = defaultdict(list)

        for child in path.children():
            low = child.lower()
            if low.endswith((".dist-info", ".egg-info")):
                # rpartition is faster than splitext and suitable for this purpose.
                name = low.rpartition(".")[0].partition("-")[0]
                normalized = Prepared.normalize(name)
                self.infos[normalized].append(path.joinpath(child))
            elif base_is_egg and low == "egg-info":
                name = base.rpartition(".")[0].partition("-")[0]
                legacy_normalized = Prepared.legacy_normalize(name)
                self.eggs[legacy_normalized].append(path.joinpath(child))

        self.infos.default_factory = None
        self.eggs.default_factory = None

    def search(self, prepared: Prepared) -> Iterable[pathlib.Path]:
        """Yield all infos and eggs matching the Prepared query."""

        if prepared:
            assert prepared.normalized is not None
            assert prepared.legacy_normalized is not None

            infos = self.infos.get(prepared.normalized, [])
            eggs = self.eggs.get(prepared.legacy_normalized, [])
        else:
            infos = chain.from_iterable(self.infos.values())
            eggs = chain.from_iterable(self.eggs.values())

        return chain(infos, eggs)


class Prepared:
    """A prepared search query for metadata on a possibly-named package.

    Pre-calculates the normalization to prevent repeated operations.

    >>> none = Prepared(None)
    >>> none.normalized
    >>> none.legacy_normalized
    >>> bool(none)
    False
    >>> sample = Prepared('Sample__Pkg-name.foo')
    >>> sample.normalized
    'sample_pkg_name_foo'
    >>> sample.legacy_normalized
    'sample__pkg_name.foo'
    >>> bool(sample)
    True
    """

    __slots__ = ("name", "normalized", "legacy_normalized")

    def __init__(self, name: _t.Optional[str]):
        self.name = name
        if name is not None:
            self.normalized = self.normalize(name)
            self.legacy_normalized = self.legacy_normalize(name)
        else:
            self.normalized = None
            self.legacy_normalized = None

    @staticmethod
    def normalize(name: str) -> str:
        """PEP 503 normalization plus dashes as underscores."""
        return re.sub(r"[-_.]+", "-", name).lower().replace("-", "_")

    @staticmethod
    def legacy_normalize(name: str) -> str:
        """Normalize the package name as found in the convention in
        older packaging tools versions and specs.
        """
        return name.lower().replace("-", "_")

    def __bool__(self):
        return bool(self.name)


@install
class MetadataPathFinder(DistributionFinder):
    """A degenerate finder for distribution packages on the file system.

    This finder supplies only a find_distributions() method for versions
    of Python that do not have a PathFinder find_distributions().
    """

    @classmethod
    def find_spec(cls, *args: _t.Any, **kwargs: _t.Any) -> None:
        """Qualify as a module finder by having this method, but defer to the rest of the meta path."""

    @classmethod
    def find_distributions(
        cls,
        context: DistributionFinder.Context = DistributionFinder.Context(),
    ) -> Iterable[PathDistribution]:
        """Find distributions.

        Return an iterable of all Distribution instances capable of
        loading the metadata for packages matching ``context.name``
        (or all names if ``None`` indicated) along the paths in the list
        of directories ``context.path``.
        """
        found = cls._search_paths(context.name, context.path)
        return map(PathDistribution, found)

    @classmethod
    def _search_paths(cls, name: _t.Optional[str], paths: list[str]) -> Iterable[pathlib.Path]:
        """Find metadata directories in paths heuristically."""
        prepared = Prepared(name)
        return chain.from_iterable(FastPath(path).search(prepared) for path in paths)

    @classmethod
    def invalidate_caches(cls) -> None:
        FastPath.__new__.cache_clear()


class PathDistribution(Distribution):
    def __init__(self, path: _meta.SimplePath) -> None:
        """Construct a distribution.

        :param path: SimplePath indicating the metadata directory.
        """
        self._path = path

    def read_text(self, filename: _StrPath) -> _t.Optional[str]:
        try:
            return self._path.joinpath(filename).read_text(encoding="utf-8")
        except (
            FileNotFoundError,
            IsADirectoryError,
            KeyError,
            NotADirectoryError,
            PermissionError,
        ):
            pass

        return None

    read_text.__doc__ = Distribution.read_text.__doc__

    def locate_file(self, path: _StrPath) -> _meta.SimplePath:
        return self._path.parent / path

    @property
    def _normalized_name(self) -> str:
        """
        Performance optimization: where possible, resolve the
        normalized name from the file system path.
        """
        stem = os.path.basename(str(self._path))
        name = self._name_from_stem(stem)
        return (Prepared.normalize(name) if (name is not None) else None) or super()._normalized_name

    @staticmethod
    def _name_from_stem(stem: str) -> _t.Optional[str]:
        """
        >>> PathDistribution._name_from_stem('foo-3.0.egg-info')
        'foo'
        >>> PathDistribution._name_from_stem('CherryPy-3.0.dist-info')
        'CherryPy'
        >>> PathDistribution._name_from_stem('face.egg-info')
        'face'
        >>> PathDistribution._name_from_stem('foo.bar')
        """
        filename, ext = os.path.splitext(stem)
        if ext not in (".dist-info", ".egg-info"):
            return None
        name, _sep, _rest = filename.partition("-")
        return name


def distribution(distribution_name: str) -> Distribution:
    """Get the ``Distribution`` instance for the named package.

    :param distribution_name: The name of the distribution package as a string.
    :return: A ``Distribution`` instance (or subclass thereof).
    """
    return Distribution.from_name(distribution_name)


def distributions(**kwargs: _t.Any) -> Iterable[Distribution]:
    """Get all ``Distribution`` instances in the current environment.

    :return: An iterable of ``Distribution`` instances.
    """
    return Distribution.discover(**kwargs)


def metadata(distribution_name: str) -> _meta.PackageMetadata:
    """Get the metadata for the named package.

    :param distribution_name: The name of the distribution package to query.
    :return: A PackageMetadata containing the parsed metadata.
    """
    return Distribution.from_name(distribution_name).metadata


def version(distribution_name: str) -> str:
    """Get the version string for the named package.

    :param distribution_name: The name of the distribution package to query.
    :return: The version string for the package as defined in the package's
        "Version" metadata key.
    """
    return distribution(distribution_name).version


def _unique(
    iterable: Iterable[Distribution],
    key: Callable[[Distribution], object] = py39.normalized_name,
) -> Generator[Distribution]:
    seen: set[object] = set()
    for item in iterable:
        normalized = key(item)
        if normalized in seen:
            continue
        seen.add(normalized)
        yield item


def entry_points(**params: _t.Any) -> EntryPoints:
    """Return EntryPoint objects for all installed packages.

    Pass selection parameters (group or name) to filter the
    result to entry points matching those properties (see
    EntryPoints.select()).

    :return: EntryPoints for all installed packages.
    """

    return EntryPoints([ep for dist in _unique(distributions()) for ep in dist.entry_points]).select(**params)


def files(distribution_name: str) -> _t.Optional[list[_path.PackagePath]]:
    """Return a list of files for the named package.

    :param distribution_name: The name of the distribution package to query.
    :return: List of files composing the distribution.
    """
    return distribution(distribution_name).files


def requires(distribution_name: str) -> _t.Optional[list[str]]:
    """
    Return a list of requirements for the named package.

    :return: An iterable of requirements, suitable for
        packaging.requirement.Requirement.
    """
    return distribution(distribution_name).requires


def packages_distributions() -> Mapping[str, list[str]]:
    """Return a mapping of top-level packages to their distributions.

    >>> import collections.abc
    >>> pkgs = packages_distributions()
    >>> all(isinstance(dist, collections.abc.Sequence) for dist in pkgs.values())
    True
    """
    pkg_to_dist: defaultdict[str, list[str]] = defaultdict(list)
    for dist in distributions():
        for pkg in _top_level_declared(dist) or _top_level_inferred(dist):
            pkg_to_dist[pkg].append(dist.metadata["Name"])
    return dict(pkg_to_dist)


def _top_level_declared(dist: Distribution) -> list[str]:
    return (dist.read_text("top_level.txt") or "").split()


def _topmost(name: _path.PackagePath) -> _t.Optional[str]:
    """
    Return the top-most parent as long as there is a parent.
    """
    top, *rest = name.parts
    return top if rest else None


def _get_toplevel_name(name: _path.PackagePath) -> str:
    """Infer a possibly importable module name from a name presumed on sys.path.

    >>> PackagePath = _path.PackagePath
    >>> _get_toplevel_name(PackagePath('foo.py'))
    'foo'
    >>> _get_toplevel_name(PackagePath('foo'))
    'foo'
    >>> _get_toplevel_name(PackagePath('foo.pyc'))
    'foo'
    >>> _get_toplevel_name(PackagePath('foo/__init__.py'))
    'foo'
    >>> _get_toplevel_name(PackagePath('foo.pth'))
    'foo.pth'
    >>> _get_toplevel_name(PackagePath('foo.dist-info'))
    'foo.dist-info'
    """

    return _topmost(name) or _getmodulename(name) or str(name)


def _getmodulename(path: _StrPath) -> _t.Optional[str]:
    """Vendored version of `inspect.getmodulename()` to avoid a heavy import.

    See original docstring below:

    Return the module name for a given file, or None.
    """

    fname = os.path.basename(path)
    # Check for paths that look like an actual module file
    suffixes = [(-len(suffix), suffix) for suffix in importlib.machinery.all_suffixes()]
    suffixes.sort()  # try longest suffixes first, in case they overlap
    for neglen, suffix in suffixes:
        if fname.endswith(suffix):
            return fname[:neglen]
    return None


def _top_level_inferred(dist: Distribution) -> Generator[str]:
    if dist.files is None:
        return

    opt_names = set(map(_get_toplevel_name, dist.files))

    for name in opt_names:
        # Ensure the names are importable.
        if "." not in name:
            yield name
