"""End-user tokens: short-lived tokens you mint for the people using your product.

Embedded mode: your backend holds a service account's key and mints, for each of
your users, a token limited to the projects (or the external user) and scopes it
names. Hand the token to that user's session; it expires in 15 minutes by
default (at most an hour) and has no refresh token, so mint another when it runs
out. A token can never do more than the credential that minted it.

Example::

    minted = beakr.end_user_tokens.mint(
        subject="customer-42", project_ids=[project_id], scopes=["kb:read"]
    )
    user_beakr = Beakr(api_key=minted["token"])
    beakr.end_user_tokens.revoke(minted["token_id"])
"""

from __future__ import annotations

import builtins
from typing import Any

from beakr_sdk._calls import adjusted, clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns, returns_all
from beakr_sdk.resources._base import Resource


class EndUserTokens(Resource[M]):
    """``/v1/auth/end-user-tokens``."""

    def mint(
        self,
        *,
        scopes: builtins.list[str],
        subject: str | None = None,
        project_ids: builtins.list[str] | None = None,
        external_user_id: str | None = None,
        ttl_seconds: int | None = None,
    ) -> Any:
        """Mint a token: ``{token, token_id, expires_at, subject, scopes, project_ids}``.

        Give ``subject`` (your own id for the user) with ``project_ids``, or an
        ``external_user_id`` (the external user's ``user_id``, a uuid; an
        ``external_id`` raises ``ValueError``) whose label and project memberships
        decide what it reaches. ``token`` is shown only in this answer. A retry mints another
        token, so the request carries no ``Idempotency-Key``.
        """
        if external_user_id is not None:
            from beakr_sdk.resources.external_users import user_id_of

            external_user_id = user_id_of(external_user_id)
        body = clean_body(
            {
                "subject": subject,
                "project_ids": project_ids,
                "external_user_id": external_user_id,
                "scopes": scopes,
                "ttl_seconds": ttl_seconds,
            }
        )
        return self._run(
            adjusted(ops.build_end_user_tokens_mint_end_user_token(body=body), idempotent=False)
        )

    @returns_all(ops.build_end_user_tokens_list_end_user_tokens)
    def list(self, *, subject: str | None = None) -> Any:
        """The live end-user tokens you minted, newest first; ``subject`` narrows to one user."""
        return self._list_all(ops.build_end_user_tokens_list_end_user_tokens(subject=subject))

    @returns(ops.build_end_user_tokens_revoke_end_user_token)
    def revoke(self, token_id: str) -> Any:
        """Revoke one token; it stops working immediately."""
        return self._run(ops.build_end_user_tokens_revoke_end_user_token(token_id))
