"""No credential-bearing object prints its secret.

A test once printed a client's credential and the full API key reached a
session's output. ``repr()``, ``str()`` and f-strings of every object that
holds an API key, an access or refresh token or an ``Authorization`` header
show it masked (``bk_live_...a1b2``): the client, the credential, a stored
sign-in, a service-account file, a request description and an error.
"""

from __future__ import annotations

import json

import httpx
import pytest

from beakr_sdk import AsyncBeakr, Beakr
from beakr_sdk._calls import Call, ListCall, oauth_form_call
from beakr_sdk._redact import mask, scrub
from beakr_sdk.credentials import (
    Credential,
    ServiceAccountFile,
    StoredTokens,
    TokenStore,
    read_service_account_file,
    resolve_credential,
)
from beakr_sdk.errors import AuthenticationError, BeakrError, error_from_response

BASE = "https://api.test"
API_KEY = "bk_live_Q7mZr2XkVbN9pL4sT8wYcH3d"
ACCESS = "bk_at_F5gJ1nR6uE0qW2zK8vM4xB7a"
REFRESH = "bk_rt_C9hT3yP6oL1sD5fG8jA2kN4e"
JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.c2lnbmF0dXJlLXNlY3JldA"
DEV_IDENTITY = "dev-identity-7b1f40c2"
SIGNATURE = "5f2b9c0e7a1d4c3b8e6f0a9d2c7b4e1f"
SECRETS = (API_KEY, ACCESS, REFRESH, JWT, DEV_IDENTITY, SIGNATURE)


def shown(obj: object) -> list[str]:
    """Every way the object is commonly formatted."""
    return [repr(obj), str(obj), f"{obj}", f"{obj!r}", format(obj)]


def assert_no_secret(obj: object) -> None:
    for text in shown(obj):
        for secret in SECRETS:
            assert secret not in text, f"{type(obj).__name__} shows a secret: {text}"
            # Not even the secret without its last four characters.
            assert secret[:-4] not in text, f"{type(obj).__name__} shows a secret: {text}"


def tokens() -> StoredTokens:
    return StoredTokens(
        access_token=ACCESS,
        refresh_token=REFRESH,
        base_url=BASE,
        token_endpoint="https://issuer.test/oauth/token",
        oauth_client_id="cli",
    )


def test_mask_keeps_the_kind_and_at_most_the_last_four_characters() -> None:
    assert mask(API_KEY) == f"bk_live_...{API_KEY[-4:]}"
    assert mask(ACCESS) == f"bk_at_...{ACCESS[-4:]}"
    assert mask(JWT) == f"...{JWT[-4:]}"
    # A short secret is masked whole.
    assert mask("bk_live_test") == "bk_live_***"
    assert mask("abc") == "***"
    assert mask(None) is None
    assert mask("") == ""


def test_an_explicit_api_key_credential_is_masked() -> None:
    credential = Credential(source="explicit", token=API_KEY)
    assert_no_secret(credential)
    assert f"bk_live_...{API_KEY[-4:]}" in repr(credential)
    assert credential.auth_headers() == {"Authorization": f"Bearer {API_KEY}"}


def test_a_device_flow_sign_in_is_masked() -> None:
    assert_no_secret(tokens())
    credential = Credential(source="cli_tokens", tokens=tokens())
    assert_no_secret(credential)
    assert "token_endpoint='https://issuer.test/oauth/token'" in repr(credential)


def test_a_dev_identity_credential_is_masked() -> None:
    credential = Credential(
        source="dev", dev_headers={"X-Identity-Id": DEV_IDENTITY, "X-Email": "dev@example.test"}
    )
    assert_no_secret(credential)
    assert "dev@example.test" in repr(credential)


def test_a_service_account_file_is_masked(tmp_path) -> None:
    path = tmp_path / "service-account.json"
    path.write_text(json.dumps({"type": "beakr_service_account", "api_key": API_KEY}))
    account = read_service_account_file(path)
    assert isinstance(account, ServiceAccountFile)
    assert_no_secret(account)


def test_every_resolved_credential_is_masked(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BEAKR_API_KEY", API_KEY)
    assert_no_secret(resolve_credential(base_url=BASE))
    monkeypatch.delenv("BEAKR_API_KEY")

    store = TokenStore()
    store.save(tokens())
    assert_no_secret(resolve_credential(base_url=BASE, token_store=store))


@pytest.mark.parametrize("client_type", [Beakr, AsyncBeakr])
def test_the_client_is_masked_before_and_after_resolving(client_type) -> None:
    beakr = client_type(api_key=API_KEY, base_url=BASE)
    assert_no_secret(beakr)
    assert "base_url='https://api.test'" in repr(beakr)
    assert beakr.credential.token == API_KEY
    assert_no_secret(beakr)
    assert_no_secret(beakr.credential)


def test_a_client_on_a_stored_sign_in_is_masked() -> None:
    store = TokenStore()
    store.save(tokens())
    beakr = Beakr(base_url=BASE, token_store=store)
    assert beakr.credential.tokens is not None
    assert_no_secret(beakr)
    # The refresh request carries the refresh token in its form body.
    assert_no_secret(beakr._refresh_call())
    beakr.close()


def test_a_request_description_is_masked() -> None:
    assert_no_secret(
        Call(
            "POST",
            "/v1/things",
            params={"access_token": ACCESS},
            json={"name": "x", "nested": [{"api_key": API_KEY}], "client_secret": JWT},
            headers={"Authorization": f"Bearer {API_KEY}", "X-Identity-Id": DEV_IDENTITY},
        )
    )
    assert_no_secret(ListCall("GET", "/v1/things", headers={"Authorization": f"Bearer {JWT}"}))
    assert_no_secret(
        oauth_form_call("https://issuer.test/token", {"refresh_token": REFRESH, "client_id": "c"})
    )
    presigned = Call(
        "PUT",
        f"https://bucket.s3.test/key?X-Amz-Signature={SIGNATURE}&X-Amz-Expires=900",
        external=True,
    )
    assert_no_secret(presigned)
    assert "X-Amz-Expires=900" in repr(presigned)


def test_an_unremarkable_request_description_is_unchanged() -> None:
    call = Call("GET", "/v1/projects", params={"limit": 5})
    assert repr(call).startswith("Call(method='GET', path='/v1/projects', params={'limit': 5}")


def test_errors_mask_a_secret_in_their_message() -> None:
    error = BeakrError(f"Key {API_KEY} was revoked; sent Authorization: Bearer {JWT}")
    assert_no_secret(error)
    assert_no_secret(AuthenticationError(f"token {ACCESS} expired", status=401))
    assert_no_secret(
        error_from_response(
            httpx.Response(
                401,
                json={"detail": f"Invalid token {REFRESH}"},
                request=httpx.Request(
                    "GET", f"{BASE}/v1/me", headers={"Authorization": f"Bearer {API_KEY}"}
                ),
            )
        )
    )


def test_scrub_masks_beakr_secrets_and_bearer_tokens_in_text() -> None:
    text = scrub(f"a {API_KEY} b Bearer {JWT} c bearer {ACCESS}")
    for secret in (API_KEY, JWT, ACCESS):
        assert secret[:-4] not in text
    assert text.startswith(f"a bk_live_...{API_KEY[-4:]} b Bearer ...{JWT[-4:]}")
    assert scrub("Project not found") == "Project not found"
