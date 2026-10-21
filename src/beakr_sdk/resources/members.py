"""Organization members.

Members live under ``/v1/organizations/{org_id}/members``; when ``org_id`` is
omitted the caller's active organization is looked up first.

Example::

    for member in beakr.members.list():
        print(member["email"], member["role"])
"""

from __future__ import annotations

from typing import Any

from beakr_sdk._calls import Flow
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk.errors import NotProvidedError
from beakr_sdk.resources._base import Resource, collect_pages

ORG_ROLES = ("owner", "admin", "member", "viewer")


class Members(Resource[M]):
    def _org_id_flow(self, org_id: str | None) -> Flow:
        if org_id:
            return org_id
        try:
            org = yield ops.build_organizations_get_active_org()
        except NotProvidedError as exc:
            raise exc.naming("organization members") from None
        return str(org["id"])

    def list(self, *, org_id: str | None = None) -> Any:
        def flow() -> Flow:
            resolved = yield from self._org_id_flow(org_id)
            return (yield from collect_pages(ops.build_organizations_list_members(resolved)))

        return self._drive(flow())

    def set_role(self, user_id: str, role: str, *, org_id: str | None = None) -> Any:
        """Change a member's role: owner, admin, member or viewer."""

        def flow() -> Flow:
            resolved = yield from self._org_id_flow(org_id)
            # The route takes the role as a query parameter, not a body field.
            return (
                yield ops.build_organizations_update_member_role(resolved, user_id, new_role=role)
            )

        return self._drive(flow())

    def remove(self, user_id: str, *, org_id: str | None = None) -> Any:
        def flow() -> Flow:
            resolved = yield from self._org_id_flow(org_id)
            return (yield ops.build_organizations_remove_member(resolved, user_id))

        return self._drive(flow())

    def add(self, user_id: str, *, role: str = "member", org_id: str | None = None) -> Any:
        """Add an existing Beakr user to the organization (admins only)."""

        def flow() -> Flow:
            resolved = yield from self._org_id_flow(org_id)
            return (
                yield ops.build_organizations_add_member(
                    resolved, body={"user_id": user_id, "role": role}
                )
            )

        return self._drive(flow())
