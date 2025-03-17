from __future__ import annotations

import email.message
import email.policy
import textwrap

from ._lazy_import import lazy_finder
from ._typing_compat import Self


with lazy_finder:
    import typing as _t


def _redent(value: str) -> str:
    """Correct for RFC822 indentation."""

    indent = " " * 8
    if not value or f"\n{indent}" not in value:
        return value
    return textwrap.dedent(indent + value)


class RawPolicy(email.policy.EmailPolicy):
    def fold(self, name: str, value: str) -> str:
        folded = self.linesep.join(
            textwrap.indent(value, prefix=" " * 8, predicate=lambda line: True).lstrip().splitlines()
        )
        return f"{name}: {folded}{self.linesep}"


class NaturalMessage(email.message.Message):
    r"""Specialized Message subclass to handle metadata naturally.

    Reads values that may have newlines in them and converts the
    payload to the Description.

    >>> msg_text = '''
    ... Name: Foo
    ... Version: 3.0
    ... License: blah
    ...         de-blah
    ... <BLANKLINE>
    ... First line of description.
    ... Second line of description.
    ... <BLANKLINE>
    ... Fourth line!
    ... '''.lstrip().replace('<BLANKLINE>', '')
    >>> msg = Message(email.message_from_string(msg_text))
    >>> msg['Description']
    'First line of description.\nSecond line of description.\n\nFourth line!\n'

    Message should render even if values contain newlines.

    >>> print(msg)
    Name: Foo
    Version: 3.0
    License: blah
            de-blah
    Description: First line of description.
            Second line of description.
    <BLANKLINE>
            Fourth line!
    <BLANKLINE>
    <BLANKLINE>
    """

    multiple_use_keys = frozenset([
        key.lower()
        for key in (
            "Classifier",
            "Obsoletes-Dist",
            "Platform",
            "Project-URL",
            "Provides-Dist",
            "Provides-Extra",
            "Requires-Dist",
            "Requires-External",
            "Supported-Platform",
            "Dynamic",
        )
    ])
    """Keys that may be indicated multiple times per PEP 566."""

    def __init__(self, *args: _t.Any, **kwargs: _t.Any) -> None:
        super().__init__(*args, **kwargs)
        self._repair_headers()

    @classmethod
    def from_original(cls, orig: email.message.Message, /) -> Self:
        self = cls.__new__(cls)
        self.__dict__ |= orig.__dict__
        self._repair_headers()
        return self

    @property
    def json(self) -> dict[str, _t.Any]:
        """Convert PackageMetadata to a JSON-compatible format per PEP 0566."""

        json_format: dict[str, _t.Any] = {}
        for key in self:
            key = key.lower()  # noqa: PLW2901
            # Message.get_all and Message.__getitem__ work case-insensitively.
            value = self.get_all(key) if key in self.multiple_use_keys else self[key]
            if key == "keywords":
                assert isinstance(value, str)
                value = value.split()
            tk = key.replace("-", "_")

            json_format[tk] = value

        return json_format

    def __getitem__(self, name: str):
        """Override parent behavior to typical dict behavior.

        ``email.message.Message`` will emit None values for missing
        keys. Typical mappings, including this ``Message``, will raise
        a key error for missing keys.

        Ref python/importlib_metadata#371.
        """
        res = super().__getitem__(name)
        if res is None:
            raise KeyError(name)
        return res

    def _repair_headers(self) -> None:
        headers: list[tuple[str, _t.Any]] = [(key, _redent(value)) for key, value in self.raw_items()]
        if payload := self.get_payload():
            headers.append(("Description", payload))
            self.set_payload("")
        self._headers = headers

    def as_string(
        self,
        unixfrom: bool = False,
        maxheaderlen: int = 0,
        policy: _t.Optional[email.policy.Policy] = None,
    ) -> str:
        if policy is None:
            policy = RawPolicy()
        return super().as_string(unixfrom, maxheaderlen, policy)
