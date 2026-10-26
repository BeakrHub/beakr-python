"""API keys: ``/v1/me/api-keys``.

Example::

    for key in beakr.keys.list():
        print(key["name"], key.get("scopes"))

Creating a key needs a signed-in web session (with recent MFA where the org
requires it): the API refuses ``create`` from an API key or a CLI access
token, so with those credentials create keys in the web app instead.
"""

from __future__ import annotations

import builtins
from typing import Any

from beakr_sdk._calls import adjusted, clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns, returns_all
from beakr_sdk.resources._base import Resource, as_list


class Keys(Resource[M]):
    def scopes(self) -> Any:
        """The scope vocabulary: ``[{name, description}]``."""
        return self._run_then(
            adjusted(ops.build_api_keys_list_scopes(), authenticated=True),
            lambda r: as_list(r, "scopes", "data"),
        )

    @returns_all(ops.build_api_keys_list_api_keys)
    def list(self) -> Any:
        return self._list_all(ops.build_api_keys_list_api_keys())

    def create(
        self,
        name: str = "default",
        *,
        scopes: builtins.list[str] | None = None,
        project_ids: builtins.list[str] | None = None,
        expires_in_days: int | None = None,
        never_expires: bool = False,
        client_name: str | None = None,
    ) -> Any:
        """Create a key. Web app only: API keys and tokens are refused (``session_required``).

        Keys are made by a signed-in person (Settings > API keys), so a leaked
        key can never mint more. This method exists for clients that hold a web
        session; from a script, create the key in the web app and pass it in.

        ``scopes=None`` asks for a full-access key, listed as ``legacy`` with a
        prompt to replace it. Every key expires: in 90 days, or after
        ``expires_in_days`` (at most 365). ``never_expires=True`` is refused
        (``non_expiring_keys_disabled``) unless an org admin allowed it.
        ``client_name`` is recorded on the key (``claude-code``, ``codex``, a CI
        system); it defaults to this SDK's name, since the API requires one.
        """
        body = clean_body(
            {
                "name": name,
                "scopes": scopes,
                "project_ids": project_ids,
                "expires_in_days": expires_in_days,
                "never_expires": True if never_expires else None,
                "client_name": self._credential_client(client_name),
            }
        )
        return self._run(ops.build_api_keys_create_api_key(body=body))

    @returns(ops.build_api_keys_revoke_api_key)
    def revoke(self, key_id: str) -> Any:
        return self._run(ops.build_api_keys_revoke_api_key(key_id))
