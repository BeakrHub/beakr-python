"""External users: the people of your product, as Beakr principals (embedded mode).

An external user is created by your backend (a service account's key or an org
admin) under your own ``external_id``. It holds no credential of its own: mint it
short-lived tokens with :meth:`beakr_sdk.resources.end_user_tokens.EndUserTokens.mint`
(``external_user_id=``), and give it projects through label membership
(:mod:`beakr_sdk.resources.labels`). Creating is idempotent on ``external_id``.

An ``external_id`` is unique only within the service account that created the
user, so it names no user on its own: :meth:`ExternalUsers.get`, ``update``,
``delete`` and token minting take the ``user_id`` (a uuid) that ``create`` and
``list`` return, and refuse anything else. ``list(external_id=...)`` finds the
user your credential created.

Example::

    person = beakr.external_users.create("customer-42", name="Ada Lovelace")
    beakr.labels.add_member(label_id, person["user_id"])
    minted = beakr.end_user_tokens.mint(external_user_id=person["user_id"], scopes=["kb:read"])
"""

from __future__ import annotations

import re
from typing import Any

from beakr_sdk._calls import Flow, clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk.resources._base import collect_pages
from beakr_sdk.resources.org_admin import _OrgScoped

_USER_ID = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")


def user_id_of(value: str) -> str:
    """``value`` as an external user's ``user_id``; an ``external_id`` raises ``ValueError``."""
    if not _USER_ID.match(value):
        raise ValueError(
            f"{value!r} is not a user_id. External users are addressed by their user_id "
            "(a uuid from create or list); find it with "
            f"external_users.list(external_id={value!r})."
        )
    return value


class ExternalUsers(_OrgScoped[M]):
    """``/v1/organizations/{org_id}/external-users``; ``org_id`` defaults to the active org."""

    def list(self, *, external_id: str | None = None, org_id: str | None = None) -> Any:
        """Every external user of the organization; ``external_id`` finds the one your
        credential created with that id."""

        def flow() -> Flow:
            resolved = yield from self._org_id_flow(org_id)
            return (
                yield from collect_pages(
                    ops.build_external_users_list_external_users(resolved, external_id=external_id)
                )
            )

        return self._drive(flow())

    def create(
        self,
        external_id: str,
        *,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
        org_id: str | None = None,
    ) -> Any:
        """Create one, or return the existing one with that ``external_id`` (``created: false``)."""
        body = clean_body({"external_id": external_id, "name": name, "metadata": metadata})
        return self._org_call(
            org_id, lambda org: ops.build_external_users_create_external_user(org, body=body)
        )

    def get(self, user_id: str, *, org_id: str | None = None) -> Any:
        return self._org_call(
            org_id, lambda org: ops.build_external_users_get_external_user(org, user_id_of(user_id))
        )

    def update(
        self,
        user_id: str,
        *,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
        org_id: str | None = None,
    ) -> Any:
        body = clean_body({"name": name, "metadata": metadata})
        return self._org_call(
            org_id,
            lambda org: ops.build_external_users_update_external_user(
                org, user_id_of(user_id), body=body
            ),
        )

    def delete(self, user_id: str, *, org_id: str | None = None) -> Any:
        """Delete one: its personal data is erased; what it put into projects stays."""
        return self._org_call(
            org_id,
            lambda org: ops.build_external_users_delete_external_user(org, user_id_of(user_id)),
        )
