"""Where Beakr credentials come from, and where device-flow tokens are kept.

The resolution chain, first match wins:

1. an explicit ``api_key`` passed to the client (a ``bk_live_`` key or a
   ``bk_at_`` access token);
2. ``BEAKR_API_KEY``;
3. a local-development identity: ``BEAKR_DEV_IDENTITY`` + ``BEAKR_DEV_EMAIL``,
   else the pair saved by ``beakr auth dev`` -- the saved pair only for the
   origin it was saved for (a record from before origins were saved applies
   only to a loopback origin, which is where ``beakr auth dev`` points);
4. the CLI's stored login for the API: a device-flow sign-in (access +
   refresh token) or a saved API key (``beakr auth login --with-api-key``),
   both in the OS keychain or ``~/.beakr/credentials`` (0600);
5. an API key an older CLI saved in ``config.json`` (the CLI moves it into the
   store the next time it runs);
6. a service-account credentials file: the path in
   ``BEAKR_SERVICE_ACCOUNT_FILE``, else ``~/.beakr/service-account.json`` when
   it exists (see :func:`read_service_account_file`).

Tokens go to the keychain through ``keyring`` when a real backend exists, and
otherwise to a 0600 file. ``BEAKR_NO_KEYRING=1`` forces the file, which is what
headless machines and the test suite use. The keychain entry is per
``BEAKR_HOME`` (:func:`_keyring_username`), so two profiles never share a login.

A stored login belongs to the API origin that issued it (production, sandbox,
a local dev server). The store keeps one login per normalized origin, and an
implicit stored credential -- a device-flow login or the saved API key -- is
only ever offered to a client pointed at that same origin, so switching
``BEAKR_BASE_URL`` can never send one deployment's bearer token to another. A
credential the caller passes explicitly is the caller's own choice and is not
bound.

Refreshing a device-flow login rotates it: the old refresh token dies the
moment the server answers, and presenting it again revokes the whole family.
Every process that refreshes therefore does so under one file lock, reloads
the store first, and adopts a pair another process already rotated instead of
spending its stale refresh token (:func:`refresh_lock`).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import stat
import tempfile
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from beakr_sdk._redact import dataclass_repr, mask, redact_headers
from beakr_sdk.errors import AuthenticationError

DEFAULT_BASE_URL = "https://api.thebeakr.com"

#: Refresh an access token this many seconds before it expires, so a request
#: never leaves with a token that dies in flight.
REFRESH_SKEW_SECONDS = 60

_KEYRING_SERVICE = "beakr-cli"
#: The keychain account of the default home (``~/.beakr``). Kept unchanged so
#: logins saved before homes were namespaced still load.
_KEYRING_USERNAME = "device-tokens"


def beakr_home() -> Path:
    """``~/.beakr`` unless ``BEAKR_HOME`` points elsewhere. Read at call time."""
    override = os.environ.get("BEAKR_HOME")
    return Path(override) if override else Path.home() / ".beakr"


def _keyring_username() -> str:
    """The keychain account holding this home's logins.

    The keychain is shared by every profile on the machine, while the config,
    the fallback file and the refresh lock live under ``BEAKR_HOME``. Without
    a per-home account, two profiles would share (and rotate) one login. The
    default home keeps the original account name; any other home gets a
    stable hash of its resolved path appended.
    """
    home = beakr_home().expanduser().resolve(strict=False)
    default = (Path.home() / ".beakr").expanduser().resolve(strict=False)
    if home == default:
        return _KEYRING_USERNAME
    digest = hashlib.sha256(str(home).encode("utf-8")).hexdigest()[:16]
    return f"{_KEYRING_USERNAME}:{digest}"


def read_cli_config() -> dict[str, Any]:
    """The CLI's ``config.json``; empty when absent or unreadable."""
    path = beakr_home() / "config.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_base_url(explicit: str | None = None) -> str:
    """Explicit, then ``BEAKR_BASE_URL`` / ``BEAKR_API_URL``, then CLI config, then prod."""
    url = (
        explicit
        or os.environ.get("BEAKR_BASE_URL")
        or os.environ.get("BEAKR_API_URL")
        or read_cli_config().get("api_url")
        or DEFAULT_BASE_URL
    )
    return str(url).rstrip("/")


_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_origin(url: str) -> str:
    """``scheme://host[:port]`` in lower case, without a default port or path.

    The key a stored login is filed under. ``https://API.thebeakr.com/`` and
    ``https://api.thebeakr.com:443`` are one origin; ``localhost`` and
    ``127.0.0.1`` are deliberately two, as they are to a browser.
    """
    parts = urlsplit(url.strip() if "://" in url else f"https://{url.strip()}")
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    if ":" in host:
        # An IPv6 literal: urlsplit drops the brackets, which the origin needs back
        # so ``http://[::1]:8000`` stays a valid, distinct origin.
        host = f"[{host}]"
    port = parts.port
    if port is None or port == _DEFAULT_PORTS.get(scheme):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def resolve_org_id(explicit: str | None = None) -> str | None:
    """Org override sent as ``X-Org-Id``. API keys are pinned to their org and ignore it."""
    return explicit or os.environ.get("BEAKR_ORG_ID") or read_cli_config().get("org_id")


# ---------------------------------------------------------------------------
# Device-flow tokens
# ---------------------------------------------------------------------------


#: ``StoredTokens.issuer`` of a saved API key: the key is the bearer, nothing refreshes it.
API_KEY_ISSUER = "api_key"


@dataclass
class StoredTokens:
    """The CLI's OAuth sign-in: an access/refresh pair with absolute expiry times."""

    access_token: str
    refresh_token: str
    expires_at: float | None = None
    refresh_expires_at: float | None = None
    base_url: str | None = None
    org_id: str | None = None
    client_name: str | None = None
    user: dict[str, Any] = field(default_factory=dict)
    #: The OAuth issuer that signed the pair in (its URL; ``clerk`` or ``beakr``
    #: in logins saved by earlier versions). It is refreshed at ``token_endpoint``
    #: and revoked at ``revocation_endpoint`` with the OAuth client id. A pair
    #: without a ``token_endpoint`` was issued by Beakr itself, which no longer
    #: signs the CLI in; it cannot be refreshed.
    issuer: str = "beakr"
    token_endpoint: str | None = None
    oauth_client_id: str | None = None
    revocation_endpoint: str | None = None
    #: ``"uncertain"`` after a refresh whose answer was lost: the server may
    #: already have rotated this refresh token, and presenting it again would
    #: revoke the whole login. Such a pair is never refreshed; sign in again.
    refresh_state: str | None = None

    @classmethod
    def from_token_response(
        cls,
        data: dict[str, Any],
        *,
        base_url: str | None = None,
        client_name: str | None = None,
        now: float | None = None,
    ) -> StoredTokens:
        """Build from an OAuth token endpoint's success body.

        ``refresh_expires_in`` and ``user``/``org_id`` are read when present (Beakr's
        own token responses carried them); an OAuth issuer usually sends neither.
        """
        issued = time.time() if now is None else now
        expires_in = data.get("expires_in")
        refresh_expires_in = data.get("refresh_expires_in")
        user = data.get("user")
        return cls(
            access_token=str(data["access_token"]),
            refresh_token=str(data.get("refresh_token") or ""),
            expires_at=issued + float(expires_in) if expires_in else None,
            refresh_expires_at=issued + float(refresh_expires_in) if refresh_expires_in else None,
            base_url=base_url,
            org_id=str(data["org_id"]) if data.get("org_id") else None,
            client_name=client_name,
            user=user if isinstance(user, dict) else {},
        )

    @classmethod
    def for_api_key(cls, api_key: str, *, base_url: str) -> StoredTokens:
        """A saved API key, kept in the store beside sign-ins (never in ``config.json``)."""
        return cls(access_token=api_key, refresh_token="", base_url=base_url, issuer=API_KEY_ISSUER)

    @property
    def is_api_key(self) -> bool:
        return self.issuer == API_KEY_ISSUER

    def access_expired(self, now: float | None = None) -> bool:
        if self.expires_at is None:
            return False
        current = time.time() if now is None else now
        return current >= self.expires_at - REFRESH_SKEW_SECONDS

    def __repr__(self) -> str:
        return dataclass_repr(self, {"access_token": mask, "refresh_token": mask})


ClearOutcome = Literal["cleared", "cleared_locally", "absent"]


class CredentialClearError(RuntimeError):
    """A logout could neither be confirmed nor recorded (nothing was written)."""


class TokenStore:
    """Persist device-flow logins, one per API origin, in the keychain or a 0600 file.

    The payload is ``{"version": 2, "generation": n, "logins": {origin: tokens}}``.
    A version-1 payload (one bare token pair) is read as the login of the origin
    it records, or of the configured API when it records none.

    Both backends can hold a copy at once: when a keychain write fails the login
    goes to the 0600 file instead, and the keychain may still hold the previous
    pair -- whose refresh token the server has already rotated away, so
    presenting it again revokes the whole login (audit C079). Every write
    therefore carries a ``generation`` one higher than any copy it read, and a
    read takes the copy with the highest generation (the keychain on a tie, and
    payloads written before generations count as 0). A later successful
    keychain write carries the newest copy back into the keychain and removes
    the file.

    Clearing is authoritative even when the keychain cannot confirm the delete
    (a locked or temporarily unreadable keychain, audit C080). A generation is
    not enough there: a keychain copy this process cannot read may carry any
    generation. So an unconfirmed clear writes a *clear barrier* -- a random
    nonce and the time of the clear -- into the file as an empty tombstone, and
    every later write carries the newest barrier it saw (``after_clear``). While
    any copy declares a barrier, a read considers only copies that declare the
    newest one (by time), whatever their generation; a stale keychain copy from
    before the clear declares none, so it can never outrank the logout.

    Logging out of one API (``clear(origin)``, what ``beakr auth logout`` does)
    needs the same guarantee without touching other APIs' logins. When the
    stored logins cannot be read -- a temporarily locked keychain -- the clear
    cannot rewrite them, so it records an *origin barrier* in a separate 0600
    file (``credentials.barriers``): ``{origin: {nonce, at}}``. A read drops that
    origin's login unless the copy it came from declares it was written after
    that barrier (``origin_after``); every other origin reads as before. A later
    write for the origin carries the barrier forward, and the barrier file is
    removed once no stale copy can remain (the keychain read-back confirms the
    copy just written, or there is no keychain).
    """

    def __init__(self, *, use_keyring: bool | None = None) -> None:
        if use_keyring is None:
            use_keyring = os.environ.get("BEAKR_NO_KEYRING", "") not in ("1", "true", "yes")
        self._use_keyring = use_keyring and _keyring_usable()
        # Whether this store may read the keychain at all. Unlike _use_keyring
        # it survives a failed write, so a fallback store still sees (and
        # outranks) the keychain's copy.
        self._keychain_readable = self._use_keyring

    @property
    def file_path(self) -> Path:
        return beakr_home() / "credentials"

    @property
    def backend(self) -> Literal["keyring", "file"]:
        return "keyring" if self._use_keyring else "file"

    def load(self, origin: str | None = None) -> StoredTokens | None:
        """The login for ``origin`` (default: the configured API's origin), or None."""
        key = normalize_origin(origin or resolve_base_url())
        return self.logins().get(key)

    def logins(self) -> dict[str, StoredTokens]:
        """Every stored login, by normalized origin, from the newest stored copy."""
        return self._logins_from(self._newest_payload()[1])

    def _logins_from(self, data: Any) -> dict[str, StoredTokens]:
        """The logins in ``data``, minus any an origin barrier has cleared."""
        found = self._raw_logins(data)
        barriers = self._origin_barriers()
        if not barriers or not found:
            return found
        after = data.get("origin_after") if isinstance(data, dict) else None
        after = after if isinstance(after, dict) else {}
        return {
            origin: tokens
            for origin, tokens in found.items()
            if origin not in barriers or after.get(origin) == barriers[origin]["nonce"]
        }

    def _raw_logins(self, data: Any) -> dict[str, StoredTokens]:
        if not isinstance(data, dict):
            return {}
        if "logins" not in data:
            legacy = _tokens_from(data)
            if legacy is None:
                return {}
            return {normalize_origin(legacy.base_url or resolve_base_url()): legacy}
        logins: dict[str, StoredTokens] = {}
        for origin, entry in (data.get("logins") or {}).items():
            tokens = _tokens_from(entry)
            if tokens is not None:
                logins[normalize_origin(origin)] = tokens
        return logins

    def save(self, tokens: StoredTokens) -> None:
        """File ``tokens`` under the origin that issued them, keeping other origins' logins."""
        if not tokens.base_url:
            tokens.base_url = resolve_base_url()
        logins = self.logins()
        logins[normalize_origin(tokens.base_url)] = tokens
        self._persist(logins)

    @property
    def barriers_path(self) -> Path:
        return beakr_home() / "credentials.barriers"

    def clear(self, origin: str | None = None) -> ClearOutcome:
        """Forget the login for ``origin``, or every login when ``origin`` is None.

        Returns what happened: ``"cleared"`` when the removal is confirmed,
        ``"cleared_locally"`` when a barrier now hides a login a backend could not
        confirm removing (logged out here; the stale copy cannot be used), and
        ``"absent"`` when there was no login for ``origin``. Raises
        :class:`CredentialClearError` when nothing could be written at all.
        """
        if origin is None:
            return "cleared" if self._delete_all() else "cleared_locally"
        key = normalize_origin(origin)
        state = self._state()
        if state.uncertain:
            # The stored logins cannot be read, so they cannot be rewritten
            # without losing other APIs' logins. Hide this origin instead (C080).
            self._add_origin_barrier(key)
            return "cleared_locally"
        logins = self._logins_from(state.data)
        if logins.pop(key, None) is None:
            return "absent"
        if logins:
            self._persist(logins)
            return "cleared"
        return "cleared" if self._delete_all() else "cleared_locally"

    def _persist(self, logins: dict[str, StoredTokens]) -> None:
        state = self._state()
        record: dict[str, Any] = {
            "version": 2,
            "generation": state.max_generation + 1,
            "logins": {origin: asdict(t) for origin, t in logins.items()},
        }
        if state.barrier is not None:
            # Carry the newest clear forward, so this copy stays eligible and
            # anything older than that clear stays shut out.
            record["after_clear"] = state.barrier
        origin_barriers = self._origin_barriers()
        after = {o: origin_barriers[o]["nonce"] for o in logins if o in origin_barriers}
        if after:
            # These logins were written after their origin's clear, so they stay
            # readable while the barrier still hides any older copy.
            record["origin_after"] = after
        payload = json.dumps(record)
        if self._use_keyring:
            import keyring

            try:
                keyring.set_password(_KEYRING_SERVICE, _keyring_username(), payload)
            except Exception:
                # A locked or broken keychain must not lose the login: the file
                # takes it, with a higher generation than the keychain's copy.
                self._use_keyring = False
                self._write_file(payload)
                # Best effort: drop the keychain's now-stale copy too. If this
                # fails the generation (and any barrier) still makes the file win.
                try:
                    keyring.delete_password(_KEYRING_SERVICE, _keyring_username())
                except Exception:
                    pass
                return
            # The keychain's one slot now holds this copy, so no stale keychain
            # copy is left. Drop the file only once that is confirmed; otherwise
            # keep the same copy in the file so the login is never only in a
            # place the next process may fail to read.
            if self._keychain_holds(payload):
                self._remove_file()
                # The keychain's only slot now holds this copy and the file is
                # gone, so no copy from before any origin barrier remains.
                self._remove_origin_barriers()
            else:
                self._write_file(payload)
            return
        self._write_file(payload)
        if not self._keychain_readable:
            # No keychain at all: the file just written is the only copy.
            self._remove_origin_barriers()

    def _delete_all(self) -> bool:
        """Forget every login, authoritatively across both backends (audit C080).

        Deleting only the copy this instance writes to is not enough: a store that
        fell back to the file still has an older keychain copy, and removing the
        file alone would hand that stale login (and its spent refresh token) to the
        next process. The keychain copy is deleted too, and the clear counts as
        confirmed only when the keychain then reads back empty (or holds the
        tombstone written in its place). A delete or read that fails is not proof
        of absence -- a temporarily unreadable keychain still holds the old copy --
        so an unconfirmed clear leaves a tombstone with a fresh clear barrier in
        the file, which outranks every copy written before it.
        """
        state = self._state()
        barrier = {"nonce": secrets.token_hex(16), "at": time.time_ns()}
        tombstone = json.dumps(
            {
                "version": 2,
                "generation": state.max_generation + 1,
                "logins": {},
                "cleared": barrier,
            }
        )
        confirmed = True
        if self._keychain_readable:
            import keyring

            try:
                keyring.delete_password(_KEYRING_SERVICE, _keyring_username())
            except Exception:
                pass
            try:
                remaining = keyring.get_password(_KEYRING_SERVICE, _keyring_username())
                readable = True
            except Exception:
                remaining, readable = None, False
            if not readable:
                confirmed = False
            elif remaining:
                try:
                    keyring.set_password(_KEYRING_SERVICE, _keyring_username(), tombstone)
                except Exception:
                    confirmed = False
                else:
                    confirmed = self._keychain_holds(tombstone)
        if confirmed:
            self._remove_file()
            self._remove_origin_barriers()
        else:
            try:
                self._write_file(tombstone)
            except OSError as exc:
                raise CredentialClearError(
                    "Could not confirm removing the stored login, and could not record "
                    f"the logout either: {exc}"
                ) from exc
        return confirmed

    def _keychain_holds(self, payload: str) -> bool:
        """Whether the keychain reads back exactly ``payload`` (False when unreadable)."""
        try:
            import keyring

            return keyring.get_password(_KEYRING_SERVICE, _keyring_username()) == payload
        except Exception:
            return False

    def _newest_payload(self) -> tuple[int, Any]:
        """``(generation, parsed payload)`` of the copy a read should use."""
        state = self._state()
        return state.generation, state.data

    def _state(self) -> _StoredState:
        """Every stored copy, reconciled (C079 generations, C080 clear barriers).

        The keychain is read whenever it is usable, even after this instance fell
        back to the file, and wins a generation tie. When any copy declares a
        clear barrier, only copies declaring the newest barrier are eligible.
        """
        copies: list[tuple[int, int, Any]] = []
        keychain_raw: str | None = None
        uncertain = False
        if self._keychain_readable:
            try:
                import keyring

                keychain_raw = keyring.get_password(_KEYRING_SERVICE, _keyring_username())
            except Exception:
                keychain_raw, uncertain = None, True
        if keychain_raw:
            copies.append((*_generation_of(keychain_raw), 1))
        try:
            file_raw: str | None = self.file_path.read_text()
        except OSError:
            file_raw = None
        if file_raw:
            copies.append((*_generation_of(file_raw), 0))
        if not copies:
            return _StoredState(0, None, 0, None, uncertain)
        max_generation = max(c[0] for c in copies)
        barriers = [b for b in (_barrier_of(c[1]) for c in copies) if b is not None]
        barrier = max(barriers, key=lambda b: (b["at"], b["nonce"])) if barriers else None
        if barrier is not None:
            copies = [
                c for c in copies if (_barrier_of(c[1]) or {}).get("nonce") == barrier["nonce"]
            ]
        generation, data, _keychain_first = max(copies, key=lambda c: (c[0], c[2]))
        return _StoredState(generation, data, max_generation, barrier, uncertain)

    def _origin_barriers(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.barriers_path.read_text())
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        barriers: dict[str, dict[str, Any]] = {}
        for origin, barrier in data.items():
            if (
                isinstance(barrier, dict)
                and isinstance(barrier.get("nonce"), str)
                and isinstance(barrier.get("at"), int)
            ):
                barriers[origin] = {"nonce": barrier["nonce"], "at": barrier["at"]}
        return barriers

    def _add_origin_barrier(self, origin: str) -> None:
        barriers = self._origin_barriers()
        barriers[origin] = {"nonce": secrets.token_hex(16), "at": time.time_ns()}
        try:
            write_private_file(self.barriers_path, json.dumps(barriers))
        except OSError as exc:
            raise CredentialClearError(
                f"Could not read the stored logins or record the logout for {origin}: {exc}"
            ) from exc

    def _remove_origin_barriers(self) -> None:
        try:
            self.barriers_path.unlink()
        except FileNotFoundError:
            pass

    def _read_raw(self) -> str | None:
        data = self._newest_payload()[1]
        return json.dumps(data) if data is not None else None

    def _write_file(self, payload: str) -> None:
        write_private_file(self.file_path, payload)

    def _remove_file(self) -> None:
        try:
            self.file_path.unlink()
        except FileNotFoundError:
            pass


def _generation_of(raw: str) -> tuple[int, Any]:
    """``(generation, parsed payload)``; unparseable or pre-generation copies are 0."""
    try:
        data = json.loads(raw)
    except ValueError:
        return 0, None
    if not isinstance(data, dict):
        return 0, None
    generation = data.get("generation")
    return (generation if isinstance(generation, int) and generation >= 0 else 0), data


def _barrier_of(data: Any) -> dict[str, Any] | None:
    """The clear barrier a payload declares: its own (``cleared``) or the one it follows."""
    if not isinstance(data, dict):
        return None
    for key in ("cleared", "after_clear"):
        barrier = data.get(key)
        if (
            isinstance(barrier, dict)
            and isinstance(barrier.get("nonce"), str)
            and isinstance(barrier.get("at"), int)
        ):
            return {"nonce": barrier["nonce"], "at": barrier["at"]}
    return None


@dataclass(frozen=True)
class _StoredState:
    generation: int
    # The stored logins themselves: tokens, never shown.
    data: Any = field(repr=False)
    max_generation: int
    barrier: dict[str, Any] | None
    # The keychain could not be read, so the stored logins are unknown.
    uncertain: bool = False


def _tokens_from(entry: Any) -> StoredTokens | None:
    if not isinstance(entry, dict):
        return None
    try:
        return StoredTokens(**entry)
    except TypeError:
        return None


def write_private_file(path: Path, payload: str) -> None:
    """Replace ``path`` atomically with ``payload``, readable only by the owner.

    Written to a 0600 temporary file in the same directory, flushed to disk,
    then renamed over the target. A concurrent reader sees the old file or the
    new one, never a truncated one -- a half-written credentials file would
    read as "no login" and push a process down to a different credential.
    """
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(directory, stat.S_IRWXU)
    # mkstemp creates the file 0600 from the start.
    fd, temp_name = tempfile.mkstemp(dir=directory, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise
    with contextlib.suppress(OSError):
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def refresh_lock_path() -> Path:
    return beakr_home() / "credentials.lock"


#: How long a refresh waits for another process's refresh before giving up.
#: A refresh is one HTTP round trip; a holder past this is stuck, and waiting
#: forever would wedge every SDK and CLI process sharing the login.
REFRESH_LOCK_TIMEOUT_SECONDS = 60.0
_POLL_START_SECONDS = 0.01
_POLL_MAX_SECONDS = 0.25


class RefreshLockTimeout(TimeoutError):
    """Another process held the refresh lock past ``REFRESH_LOCK_TIMEOUT_SECONDS``."""


class RefreshLock:
    """The cross-process lock every stored-login refresh runs under.

    ``flock`` on an open file: the kernel releases it if the process dies, so a
    crashed refresh never wedges the next one. On a platform without ``fcntl``
    (Windows) refreshes are serialized only within the process.

    ``acquire`` polls a non-blocking ``flock`` with backoff until ``timeout``
    (``RefreshLockTimeout``), so a waiter is never parked in an uninterruptible
    blocking call; the async client runs it on a worker thread and hands any
    lock acquired after the awaiter was cancelled straight back (audit C055).
    A lock still held when the object is collected is released then.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or refresh_lock_path()
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self, timeout: float | None = REFRESH_LOCK_TIMEOUT_SECONDS) -> None:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows
            return
        import time

        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, stat.S_IRUSR | stat.S_IWUSR)
        deadline = None if timeout is None else time.monotonic() + timeout
        pause = _POLL_START_SECONDS
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise RefreshLockTimeout(
                            f"another Beakr process held {self._path} for over {timeout:g}s"
                        ) from None
                    time.sleep(pause)
                    pause = min(pause * 2, _POLL_MAX_SECONDS)
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        import fcntl

        fd, self._fd = self._fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> RefreshLock:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()

    def __del__(self) -> None:
        # Closing the fd drops the flock; never leave one held by a lost object.
        fd = getattr(self, "_fd", None)
        if fd is not None:
            self._fd = None
            with contextlib.suppress(OSError):
                os.close(fd)


@contextlib.contextmanager
def refresh_lock() -> Iterator[None]:
    with RefreshLock():
        yield


def _keyring_usable() -> bool:
    """True when keyring has a real backend (not the fail or null backend)."""
    try:
        import keyring
        from keyring.backends import fail
    except Exception:
        return False
    try:
        backend = keyring.get_keyring()
    except Exception:
        return False
    if isinstance(backend, fail.Keyring):
        return False
    return type(backend).__module__ not in ("keyring.backends.null",)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

CredentialSource = Literal["explicit", "dev", "env", "cli_tokens", "cli_config", "service_account"]

#: ``type`` of a service-account credentials file.
SERVICE_ACCOUNT_FILE_TYPE = "beakr_service_account"
SERVICE_ACCOUNT_FILE_ENV = "BEAKR_SERVICE_ACCOUNT_FILE"


@dataclass(frozen=True)
class ServiceAccountFile:
    """A service account's key, saved for a CI job or server (developer platform W6.1).

    The file is JSON, written 0600 by ``beakr org service-accounts key
    --credentials-file`` or :func:`write_service_account_file`::

        {"type": "beakr_service_account", "api_key": "bk_live_...",
         "base_url": "https://api.thebeakr.com", "org_id": "...",
         "service_account_id": "...", "key_id": "...", "name": "deploy"}

    Only ``type`` and ``api_key`` are required. ``base_url``, when present,
    binds the key to that API exactly as a stored login is bound: the key is
    never offered to a client pointed elsewhere.
    """

    path: Path
    api_key: str
    base_url: str | None = None
    org_id: str | None = None
    service_account_id: str | None = None
    key_id: str | None = None
    name: str | None = None

    def __repr__(self) -> str:
        return dataclass_repr(self, {"api_key": mask})


def service_account_file_path() -> tuple[Path, bool]:
    """The file to read, and whether it was named explicitly (by the env var)."""
    named = os.environ.get(SERVICE_ACCOUNT_FILE_ENV)
    if named:
        return Path(named).expanduser(), True
    return beakr_home() / "service-account.json", False


def read_service_account_file(path: Path) -> ServiceAccountFile:
    """Parse a service-account credentials file; raise ``AuthenticationError`` if unusable."""
    try:
        data = json.loads(path.read_text())
    except OSError as exc:
        raise AuthenticationError(
            f"Cannot read the service account file {path}: {exc.strerror or exc}.",
            code="unauthorized",
        ) from exc
    except ValueError as exc:
        raise AuthenticationError(
            f"The service account file {path} is not valid JSON.", code="unauthorized"
        ) from exc
    if not isinstance(data, dict) or data.get("type") != SERVICE_ACCOUNT_FILE_TYPE:
        raise AuthenticationError(
            f"{path} is not a Beakr service account file "
            f'(expected "type": "{SERVICE_ACCOUNT_FILE_TYPE}").',
            code="unauthorized",
        )
    api_key = data.get("api_key")
    if not isinstance(api_key, str) or not api_key.startswith(("bk_live_", "bk_test_")):
        raise AuthenticationError(
            f"The service account file {path} has no api_key (bk_live_... or bk_test_...).",
            code="unauthorized",
        )

    def text(name: str) -> str | None:
        value = data.get(name)
        return str(value) if value else None

    return ServiceAccountFile(
        path=path,
        api_key=api_key,
        base_url=text("base_url"),
        org_id=text("org_id"),
        service_account_id=text("service_account_id"),
        key_id=text("key_id"),
        name=text("name"),
    )


def write_service_account_file(
    path: Path, minted: dict[str, Any], *, base_url: str, org_id: str | None = None
) -> Path:
    """Save a freshly minted service-account key (shown once) as a 0600 credentials file."""
    key = minted.get("key")
    if not key:
        raise ValueError("The minted key response carries no key (was it a replay?).")
    payload = {
        "type": SERVICE_ACCOUNT_FILE_TYPE,
        "api_key": key,
        "base_url": base_url.rstrip("/"),
        "org_id": org_id,
        "service_account_id": minted.get("user_id"),
        "key_id": minted.get("id"),
        "name": minted.get("name"),
        "scopes": minted.get("scopes"),
        "expires_at": minted.get("expires_at"),
    }
    write_private_file(path, json.dumps({k: v for k, v in payload.items() if v}, indent=2))
    return path


@dataclass
class Credential:
    """The credential a client authenticates with, and where it came from."""

    source: CredentialSource
    token: str | None = None
    tokens: StoredTokens | None = None
    dev_headers: dict[str, str] = field(default_factory=dict)

    @property
    def can_refresh(self) -> bool:
        return self.tokens is not None

    @property
    def kind(self) -> Literal["api_key", "access_token", "dev_identity"]:
        if self.dev_headers:
            return "dev_identity"
        if self.tokens is not None or (self.token or "").startswith("bk_at_"):
            return "access_token"
        return "api_key"

    def auth_headers(self) -> dict[str, str]:
        if self.dev_headers:
            return dict(self.dev_headers)
        bearer = self.tokens.access_token if self.tokens else self.token
        return {"Authorization": f"Bearer {bearer}"} if bearer else {}

    def __repr__(self) -> str:
        # ``tokens`` formats itself redacted.
        return dataclass_repr(self, {"token": mask, "dev_headers": redact_headers})


#: Hosts a legacy saved dev identity (one saved without ``dev_origin``) may be
#: sent to. ``beakr auth dev`` has always targeted a local server.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_loopback_origin(origin: str) -> bool:
    return (urlsplit(origin).hostname or "").lower() in _LOOPBACK_HOSTS


def _saved_dev_identity_applies(config: dict[str, Any], origin: str) -> bool:
    """Whether the dev identity saved by ``beakr auth dev`` belongs to ``origin``.

    A saved dev identity is a stored credential like a login, so it is bound to
    the API it was saved for: otherwise a leftover ``beakr auth dev`` against
    localhost would shadow every real credential for production too.
    """
    saved_origin = config.get("dev_origin")
    if saved_origin:
        return normalize_origin(str(saved_origin)) == origin
    return _is_loopback_origin(origin)


def _dev_identity_headers(config: dict[str, Any], origin: str) -> dict[str, str]:
    """The identity headers for ``origin``, or ``{}`` when no dev identity applies.

    The environment pair is the caller's explicit choice and applies to any
    origin (like ``BEAKR_API_KEY``); the saved pair only to its own origin.
    """
    env_id = os.environ.get("BEAKR_DEV_IDENTITY")
    env_email = os.environ.get("BEAKR_DEV_EMAIL")
    identity_id: object
    email: object
    if env_id and env_email:
        identity_id, email = env_id, env_email
    elif _saved_dev_identity_applies(config, origin):
        identity_id = env_id or config.get("dev_identity_id")
        email = env_email or config.get("dev_email")
    else:
        return {}
    if not (identity_id and email):
        return {}
    headers = {"X-Identity-Id": str(identity_id), "X-Email": str(email)}
    # The dev API keys the user on the identity NAME as well as the id and
    # defaults a missing one to "seed", silently provisioning a user who is a
    # member of nothing. Forward it whenever it is configured.
    name = os.environ.get("BEAKR_DEV_IDENTITY_NAME") or config.get("dev_identity_name")
    if name:
        headers["X-Identity-Name"] = str(name)
    display = os.environ.get("BEAKR_DEV_DISPLAY_NAME") or config.get("dev_display_name")
    if display:
        headers["X-Display-Name"] = str(display)
    return headers


def resolve_credential(
    api_key: str | None = None,
    *,
    token_store: TokenStore | None = None,
    base_url: str | None = None,
) -> Credential:
    """Walk the chain in the module docstring; raise when nothing is configured.

    ``base_url`` is the API the credential is for (default: the configured
    one). Stored credentials -- the saved dev identity, the device-flow login
    and the saved API key -- are offered only when they belong to that origin.
    """
    if api_key:
        return Credential(source="explicit", token=api_key)
    env_key = os.environ.get("BEAKR_API_KEY")
    if env_key:
        return Credential(source="env", token=env_key)
    config = read_cli_config()
    origin = normalize_origin(base_url or resolve_base_url())
    dev = _dev_identity_headers(config, origin)
    if dev:
        return Credential(source="dev", dev_headers=dev)
    store = token_store or TokenStore()
    tokens = store.load(origin)
    if tokens is not None and tokens.is_api_key:
        return Credential(source="cli_config", token=tokens.access_token)
    if tokens is not None:
        return Credential(source="cli_tokens", tokens=tokens)
    saved_key = config.get("api_key")
    if saved_key and normalize_origin(str(config.get("api_url") or DEFAULT_BASE_URL)) == origin:
        return Credential(source="cli_config", token=str(saved_key))
    elsewhere = sorted(store.logins())
    sa_path, named = service_account_file_path()
    if named or sa_path.exists():
        account = read_service_account_file(sa_path)
        if account.base_url is None or normalize_origin(account.base_url) == origin:
            return Credential(source="service_account", token=account.api_key)
        elsewhere.append(normalize_origin(account.base_url))
    if saved_key:
        elsewhere.append(normalize_origin(str(config.get("api_url") or DEFAULT_BASE_URL)))
    if elsewhere:
        raise AuthenticationError(
            f"No Beakr credentials for {origin}. The stored sign-in is for "
            f"{', '.join(sorted(set(elsewhere)))}, and a stored credential is only "
            f"sent to the API that issued it. Run `beakr auth login` against {origin}, "
            "or pass api_key= to the client.",
            code="unauthorized",
        )
    raise AuthenticationError(
        "No Beakr credentials found. Run `beakr auth login`, set BEAKR_API_KEY or "
        f"{SERVICE_ACCOUNT_FILE_ENV}, or pass api_key= to the client.",
        code="unauthorized",
    )
