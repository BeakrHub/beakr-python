"""Keep secrets out of ``repr()``, ``str()`` and error messages.

A credential printed by a test, a log line or a traceback reaches whatever
reads that output (a CI log, an agent's transcript). Every object here that
holds an API key, an access or refresh token or an ``Authorization`` header
formats the secret through :func:`mask`, which keeps only what identifies it:
its kind (``bk_live_``) and at most its last four characters
(``bk_live_...a1b2``). A short secret is masked whole.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode

#: Prefixes of Beakr secrets; kept in a masked value because they name its kind.
SECRET_PREFIXES = ("bk_live_", "bk_test_", "bk_at_", "bk_rt_")

#: A secret shorter than this past its prefix is masked whole: four characters
#: of a short secret are too large a share of it.
_MIN_TAIL_LENGTH = 16

_MASK = "***"

#: Header, body field and query parameter names whose values are secrets
#: (``Authorization``, ``refresh_token``, ``api_key``, ``client_secret``, a
#: presigned URL's ``X-Amz-Signature``).
_SECRET_NAME = re.compile(
    r"(?i)authorization|cookie|token|secret|password|api[_-]?key|credential|signature"
)

#: A local-development identity id authenticates as that person on a dev server.
_SECRET_HEADERS = frozenset({"x-identity-id"})

#: Secrets as they appear in free text: a Beakr key or token, or a bearer token.
_TEXT_SECRETS = re.compile(
    r"(?P<beakr>\bbk_(?:live|test|at|rt)_[A-Za-z0-9_\-]+)"
    r"|(?P<scheme>\bBearer\s+)(?P<bearer>[A-Za-z0-9._~+/=\-]+)",
    re.IGNORECASE,
)


def mask(secret: str | None) -> str | None:
    """``bk_live_...a1b2`` for ``bk_live_<secret>a1b2``; ``None`` and ``""`` unchanged."""
    if not secret:
        return secret
    prefix = next((p for p in SECRET_PREFIXES if secret.startswith(p)), "")
    body = secret[len(prefix) :]
    if len(body) < _MIN_TAIL_LENGTH:
        return f"{prefix}{_MASK}"
    return f"{prefix}...{body[-4:]}"


def is_secret_name(name: str) -> bool:
    """Whether a header or field called ``name`` carries a secret."""
    return name.lower() in _SECRET_HEADERS or bool(_SECRET_NAME.search(name))


def mask_header(name: str, value: str) -> str:
    """One header's value as it may be shown (``Bearer bk_live_...a1b2``)."""
    if not is_secret_name(name):
        return value
    scheme, space, credential = value.partition(" ")
    if space and scheme.lower() in {"bearer", "basic", "token"}:
        return f"{scheme} {mask(credential)}"
    return mask(value) or value


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """``headers`` with every secret value masked."""
    return {name: mask_header(name, value) for name, value in headers.items()}


def redact_fields(value: Any) -> Any:
    """A JSON-like ``value`` with the values of secret-named fields masked."""
    if isinstance(value, Mapping):
        return {
            key: _mask_any(item) if is_secret_name(str(key)) else redact_fields(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_fields(item) for item in value]
    return value


def redact_url(url: str) -> str:
    """``url`` with the values of secret query parameters masked (a presigned URL's)."""
    base, mark, query = url.partition("?")
    if not mark:
        return url
    pairs = parse_qsl(query, keep_blank_values=True)
    if not any(is_secret_name(name) for name, _ in pairs):
        return url
    shown = [(name, _mask_any(value) if is_secret_name(name) else value) for name, value in pairs]
    return f"{base}?{urlencode(shown, safe='.')}"


def _mask_any(value: Any) -> Any:
    return mask(value) if isinstance(value, str) else _MASK if value else value


def scrub(text: str) -> str:
    """``text`` with every Beakr key or token and bearer token in it masked."""

    def replace(match: re.Match[str]) -> str:
        if match.group("beakr"):
            return mask(match.group("beakr")) or ""
        return f"{match.group('scheme')}{mask(match.group('bearer'))}"

    return _TEXT_SECRETS.sub(replace, text)


class Shown:
    """A stand-in whose ``repr`` is ``text`` (``<120 bytes>`` in place of a body)."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __repr__(self) -> str:
        return self.text


def dataclass_repr(obj: Any, redact: Mapping[str, Callable[[Any], Any]]) -> str:
    """The dataclass's usual ``repr`` with the fields in ``redact`` passed through it."""
    shown = []
    for field in dataclasses.fields(obj):
        if not field.repr:
            continue
        value = getattr(obj, field.name)
        if field.name in redact:
            value = redact[field.name](value)
        shown.append(f"{field.name}={value!r}")
    return f"{type(obj).__qualname__}({', '.join(shown)})"
