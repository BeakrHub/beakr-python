"""Organizations the caller belongs to: ``client.organizations``.

Member management lives in :mod:`beakr_sdk.resources.members` and admin
controls in :mod:`beakr_sdk.resources.org_admin`; this is the organization
itself.
"""

from __future__ import annotations

from typing import Any

from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns, returns_all
from beakr_sdk.resources._base import Resource


class Organizations(Resource[M]):
    @returns_all(ops.build_organizations_list_orgs)
    def list(self) -> Any:
        """Every organization you are a member of."""
        return self._list_all(ops.build_organizations_list_orgs())

    @returns(ops.build_organizations_get_org)
    def get(self, org_id: str) -> Any:
        return self._run(ops.build_organizations_get_org(org_id))
