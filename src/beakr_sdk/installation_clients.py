"""The client versions a self-hosted installation's release ships.

A full self-hosted deployment serves its own release's clients from the same host
as the app (beakr-engine ``client_index.py``, served by its web image):
``GET /clients/manifest.json`` names the release and, for each client package,
the exact stamped version (``0.4.0+selfhosted.g<commit>``), its files and their
sha256, and how to install it from the installation's own package index
(``/pypi/simple/``, ``/npm/``). The manifest sits outside ``/v1`` and needs no
credential: none is sent.

``beakr.meta.installation_clients()`` reads it (``None`` from a server that serves
none: hosted Beakr, or an older release). ``beakr.meta.check_installation_clients()``
also warns, once per process and installation, when this beakr-sdk is not the
version the installation ships. Nothing here runs on its own: constructing a
client makes no request.
"""

from __future__ import annotations

import json
import threading
import time
import warnings
from collections.abc import Callable, Mapping
from typing import Any

from beakr_sdk._calls import Call, Flow
from beakr_sdk._version import __version__
from beakr_sdk.errors import BeakrError

__all__ = [
    "MANIFEST_PATH",
    "InstallationClients",
    "InstallationClientsWarning",
    "check_installation_clients_flow",
    "forget_installation_clients",
    "installation_clients_flow",
]

MANIFEST_PATH = "/clients/manifest.json"
SCHEMA_VERSION = 1
SDK_PACKAGE = "beakr-sdk"
#: How long a read manifest (or a failure to read one) is kept before asking again.
TTL_SECONDS = 300.0
#: The literal placeholder the manifest's install commands carry for the installation.
ORIGIN_PLACEHOLDER = "{origin}"


class InstallationClientsWarning(UserWarning):
    """This client is not the version the installation's release ships."""


class InstallationClients:
    """One installation's ``/clients/manifest.json``: its release and client versions."""

    def __init__(self, base_url: str, manifest: Mapping[str, Any]) -> None:
        self.base_url = base_url.rstrip("/")
        #: The manifest as served.
        self.manifest: Mapping[str, Any] = manifest
        #: The installation's release version (``1.0.7``).
        self.release: str = str(manifest["release"])
        clients = manifest.get("clients")
        self._clients: Mapping[str, Any] = clients if isinstance(clients, Mapping) else {}

    @classmethod
    def parse(cls, base_url: str, data: Any) -> InstallationClients | None:
        """The manifest, or None for anything that is not a schema 1 manifest."""
        if not isinstance(data, Mapping) or data.get("schema_version") != SCHEMA_VERSION:
            return None
        if not isinstance(data.get("release"), str) or not data["release"]:
            return None
        if not isinstance(data.get("clients"), Mapping):
            return None
        return cls(base_url, data)

    @property
    def packages(self) -> list[str]:
        """The client packages the release ships (``beakr-cli``, ``beakr-sdk``, ``@beakr/sdk``)."""
        return sorted(self._clients)

    def version(self, package: str = SDK_PACKAGE) -> str | None:
        """The exact version of ``package`` the release ships, or None."""
        entry = self._clients.get(package)
        value = entry.get("version") if isinstance(entry, Mapping) else None
        return value if isinstance(value, str) and value else None

    def install_command(self, package: str = SDK_PACKAGE) -> str | None:
        """How to install exactly that version, with this installation's address filled in."""
        entry = self._clients.get(package)
        command = entry.get("install") if isinstance(entry, Mapping) else None
        if not isinstance(command, str) or not command:
            return None
        return command.replace(ORIGIN_PLACEHOLDER, self.base_url)

    @property
    def sdk_matches(self) -> bool | None:
        """Whether this beakr-sdk is the one the release ships (None when it names none)."""
        expected = self.version(SDK_PACKAGE)
        return None if expected is None else expected == __version__

    def __repr__(self) -> str:
        versions = ", ".join(f"{name} {self.version(name)}" for name in self.packages)
        return f"InstallationClients(release={self.release!r}, {versions})"


_cache: dict[str, tuple[float, InstallationClients | None]] = {}
_warned: set[tuple[str, str]] = set()
_lock = threading.Lock()


def _key(base_url: str) -> str:
    return base_url.strip().rstrip("/").lower()


def forget_installation_clients(base_url: str | None = None) -> None:
    """Drop the cached manifest (and the once-only warning) for ``base_url``, or all."""
    with _lock:
        if base_url is None:
            _cache.clear()
            _warned.clear()
        else:
            _cache.pop(_key(base_url), None)
            for item in [item for item in _warned if item[0] == _key(base_url)]:
                _warned.discard(item)


def installation_clients_flow(
    base_url: str,
    *,
    refresh: bool = False,
    clock: Callable[[], float] = time.monotonic,
) -> Flow:
    """A flow returning ``base_url``'s :class:`InstallationClients`, or None.

    Any failure (no such file, a sign-in page, another schema, unreachable) is
    None, cached for :data:`TTL_SECONDS` like a manifest.
    """
    key = _key(base_url)
    with _lock:
        cached = _cache.get(key)
    if cached is not None and not refresh and clock() - cached[0] < TTL_SECONDS:
        return cached[1]
    try:
        text = yield Call("GET", MANIFEST_PATH, authenticated=False, response="text")
        found = InstallationClients.parse(base_url, json.loads(text) if text else None)
    except (BeakrError, ValueError, TypeError):
        found = None
    with _lock:
        _cache[key] = (clock(), found)
    return found


def check_installation_clients_flow(base_url: str, *, refresh: bool = False) -> Flow:
    """:func:`installation_clients_flow`, warning once when this beakr-sdk differs."""
    found = yield from installation_clients_flow(base_url, refresh=refresh)
    if found is None or found.sdk_matches is not False:
        return found
    expected = found.version(SDK_PACKAGE) or ""
    with _lock:
        first = (_key(base_url), expected) not in _warned
        _warned.add((_key(base_url), expected))
    if first:
        command = found.install_command(SDK_PACKAGE)
        warnings.warn(
            f"This installation (release {found.release}) ships {SDK_PACKAGE} {expected}; "
            f"you have {__version__}." + (f" Upgrade: {command}" if command else ""),
            InstallationClientsWarning,
            stacklevel=3,
        )
    return found
