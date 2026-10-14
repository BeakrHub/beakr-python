"""The signed-in caller: ``/v1/me`` and the active organization."""

from __future__ import annotations

from typing import Any

from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns
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
