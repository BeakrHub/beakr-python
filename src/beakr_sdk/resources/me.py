"""The signed-in caller: ``/v1/me`` and the active organization."""

from __future__ import annotations

from typing import Any

from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns
from beakr_sdk.capabilities import capabilities_flow
from beakr_sdk.installation_clients import (
    check_installation_clients_flow,
    installation_clients_flow,
)
from beakr_sdk.resources._base import Resource


class Me(Resource[M]):
    @returns(ops.build_account_whoami)
    def get(self) -> Any:
        """``{user, personal_org, ...}``."""
        return self._run(ops.build_account_whoami())

    @returns(ops.build_organizations_get_active_org)
    def active_org(self) -> Any:
        """The organization requests run in (the key's org, or the session's active org)."""
        return self._run(ops.build_organizations_get_active_org())


class Meta(Resource[M]):
    """The API describing itself."""

    @returns(ops.build_openapi_public_openapi)
    def openapi(self) -> Any:
        """The public OpenAPI document (every operation a key or token may call)."""
        return self._run(ops.build_openapi_public_openapi())

    def capabilities(self, *, refresh: bool = False) -> Any:
        """Which documented operations this server serves, as a ``Capabilities``.

        Read from the server's own OpenAPI document, fetched at most once per
        base URL every few minutes (``refresh=True`` reads it again now). A
        self-hosted installation serves less than the hosted service:
        ``beakr.meta.capabilities().serves("GET", "/v1/integrations")`` says
        whether this one has integrations before a call answers 404.
        """
        return self._drive(capabilities_flow(self._client.base_url, refresh=refresh))

    def installation_clients(self, *, refresh: bool = False) -> Any:
        """The client versions this installation's release ships, or None.

        A self-hosted installation serves its release's manifest at
        ``/clients/manifest.json`` (no credential is sent): its release and, per
        client package, the exact version and how to install it from the
        installation's own package index. Returns an ``InstallationClients``
        (``.release``, ``.version("beakr-sdk")``, ``.install_command(...)``), or
        None from a server that serves none (hosted Beakr, an older release).
        Kept per base URL for a few minutes; ``refresh=True`` reads it again.
        """
        return self._drive(installation_clients_flow(self._client.base_url, refresh=refresh))

    def check_installation_clients(self, *, refresh: bool = False) -> Any:
        """:meth:`installation_clients`, warning once if this beakr-sdk is not the one shipped.

        The warning (``InstallationClientsWarning``) names the version the
        installation's release ships and the command that installs it; it is
        given once per process for each installation and version.
        """
        return self._drive(check_installation_clients_flow(self._client.base_url, refresh=refresh))
