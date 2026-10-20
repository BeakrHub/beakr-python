"""The signed-in caller: ``/v1/me`` and the active organization."""

from __future__ import annotations

from typing import Any

from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns
from beakr_sdk.capabilities import capabilities_flow
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
