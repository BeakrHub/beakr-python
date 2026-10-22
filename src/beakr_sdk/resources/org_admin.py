"""Organization admin controls: credentials, service accounts and auth policy.

Every route lives under ``/v1/organizations/{org_id}`` and must name the org the
request runs in (the engine answers 404 for any other id), and every route
requires an organization admin. Creating service accounts, minting their keys
and changing the policy also require a signed-in browser session, and a fresh
second factor when the org's policy asks for step-up; the API refuses them to
keys and tokens with ``session_required`` / ``mfa_required``.

When ``org_id`` is omitted it is resolved, in order, from the client's org
override (``BEAKR_ORG_ID`` or the CLI config), the org a CLI sign-in was
approved for, and finally ``/v1/me/organization``.

Example::

    for credential in beakr.org.credentials.list():
        print(credential["kind"], credential["name"], credential["last_used_at"])
    bot = beakr.org.service_accounts.create("CI bot")
    key = beakr.org.service_accounts.mint_key(bot["user_id"], scopes=["kb:read"])
    print(key["key"])  # shown once
"""

from __future__ import annotations

import builtins
from collections.abc import Callable
from typing import Any, Generic

from beakr_sdk._calls import Call, Flow, clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk.errors import NotProvidedError
from beakr_sdk.resources._base import Resource, collect_pages

SERVICE_ACCOUNT_ROLES = ("member", "admin", "viewer")
# "Not passed": distinct from None, which is a meaningful value for some fields.
_UNSET: Any = object()


class _OrgScoped(Resource[M]):
    #: What a refusal names when the active-organization lookup is not provided.
    _AREA = "organization administration"

    def _org_id_flow(self, org_id: str | None) -> Flow:
        if org_id:
            return org_id
        client = self._client
        if getattr(client, "org_id", None):
            return str(client.org_id)
        tokens = getattr(getattr(client, "credential", None), "tokens", None)
        if tokens is not None and tokens.org_id:
            return str(tokens.org_id)
        try:
            org = yield ops.build_organizations_get_active_org()
        except NotProvidedError as exc:
            raise exc.naming(self._AREA) from None
        return str(org["id"])

    def _read_modify_write(
        self,
        org_id: str | None,
        read: Callable[[str], Call],
        write: Callable[..., Call],
        changes: dict,
        accepted: tuple[str, ...],
    ) -> Any:
        """GET the stored document, overlay ``changes``, PUT the accepted fields (one flow).

        Only ``accepted`` fields are sent back: the GET also reports read-only
        facts (floors, approved apps) the PUT does not take.
        """

        def flow() -> Flow:
            resolved = yield from self._org_id_flow(org_id)
            current = yield read(resolved)
            stored = current if isinstance(current, dict) else {}
            body = {name: stored[name] for name in accepted if name in stored}
            body.update(changes)
            return (yield write(resolved, body=body))

        return self._drive(flow())

    def _org_call(self, org_id: str | None, build: Callable[[str], Call]) -> Any:
        """Resolve the organization, then send ``build(org_id)``."""

        def flow() -> Flow:
            resolved = yield from self._org_id_flow(org_id)
            return (yield build(resolved))

        return self._drive(flow())


class ServiceAccounts(_OrgScoped[M]):
    """Non-human members that own scoped keys, for automation and CI."""

    _AREA = "service accounts"

    def list(self, *, org_id: str | None = None) -> Any:
        def flow() -> Flow:
            resolved = yield from self._org_id_flow(org_id)
            return (
                yield from collect_pages(ops.build_service_accounts_list_service_accounts(resolved))
            )

        return self._drive(flow())

    def create(self, name: str, *, role: str = "member", org_id: str | None = None) -> Any:
        """Create a service account. Requires a browser session (not a key or token)."""
        return self._org_call(
            org_id,
            lambda org: ops.build_service_accounts_create_service_account(
                org, body={"name": name, "role": role}
            ),
        )

    def disable(self, account_id: str, *, org_id: str | None = None) -> Any:
        """Disable the account and revoke every key it owns."""
        return self._org_call(
            org_id, lambda org: ops.build_service_accounts_disable_service_account(org, account_id)
        )

    def mint_key(
        self,
        account_id: str,
        *,
        scopes: builtins.list[str],
        name: str | None = None,
        project_ids: builtins.list[str] | None = None,
        expires_in_days: int | None = None,
        client_name: str | None = None,
        org_id: str | None = None,
    ) -> Any:
        """Mint a scoped key owned by the service account. ``key`` is returned once.

        ``client_name`` is recorded on the key (a CI system, an integration);
        it defaults to this SDK's name, since the API requires one.
        """
        body = clean_body(
            {
                "name": name,
                "scopes": scopes,
                "project_ids": project_ids,
                "expires_in_days": expires_in_days,
                "client_name": self._credential_client(client_name),
            }
        )
        return self._org_call(
            org_id,
            lambda org: ops.build_service_accounts_mint_service_account_key(
                org, account_id, body=body
            ),
        )


class OrgAdmin(Generic[M]):
    """``client.org``: the organization's service accounts."""

    def __init__(self, client: Any) -> None:
        self.service_accounts: ServiceAccounts[M] = ServiceAccounts(client)
