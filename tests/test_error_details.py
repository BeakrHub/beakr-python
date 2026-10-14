"""Typed errors expose the envelope's ``error.details`` (SPEC-117).

The server puts structured context (``required_scope``, every failing field of a
validation error under ``errors``) in ``error.details``; servers before it
existed put it beside ``code``. Both must reach the typed error.
"""

from __future__ import annotations

import httpx

from beakr_sdk.errors import PermissionDeniedError, ValidationError, error_from_response


def _response(status: int, body: object) -> httpx.Response:
    return httpx.Response(status, json=body, request=httpx.Request("GET", "https://x.test/v1"))


def test_validation_field_errors_are_exposed_from_details() -> None:
    errors = [
        {"loc": ["body", "name"], "msg": "Field required", "type": "missing"},
        {"loc": ["body", "size"], "msg": "Input should be greater than 0", "type": "greater_than"},
    ]
    error = error_from_response(
        _response(
            422,
            {
                "detail": errors,
                "error": {
                    "code": "validation_error",
                    "message": "Field required",
                    "details": {"errors": errors},
                    "request_id": "r1",
                },
            },
        )
    )
    assert isinstance(error, ValidationError)
    assert error.details == {"errors": errors}
    assert error.request_id == "r1"


def test_required_scope_is_read_from_details_then_the_envelope() -> None:
    from_details = error_from_response(
        _response(
            403,
            {
                "error": {
                    "code": "insufficient_scope",
                    "message": "m",
                    "details": {"required_scope": "kb:write"},
                }
            },
        )
    )
    assert isinstance(from_details, PermissionDeniedError)
    assert from_details.required_scope == "kb:write"
    legacy = error_from_response(
        _response(
            403,
            {"error": {"code": "insufficient_scope", "message": "m", "required_scope": "kb:read"}},
        )
    )
    assert isinstance(legacy, PermissionDeniedError)
    assert legacy.required_scope == "kb:read"
    assert legacy.details == {}


def test_details_default_to_empty_for_legacy_bodies() -> None:
    assert error_from_response(_response(404, {"detail": "Not found"})).details == {}


def test_a_quota_refusal_says_the_limit_its_window_and_the_fix() -> None:
    # Root cause (proof run): a 429 quota_exceeded said only "Uploads limit
    # reached"; the limit, window and reset the server sent in error.details,
    # and its error.fix, never reached the person reading the error.
    import httpx

    from beakr_sdk.errors import RateLimitError, error_from_response

    response = httpx.Response(
        429,
        json={
            "error": {
                "code": "quota_exceeded",
                "message": "Uploads limit reached",
                "fix": "Wait for the window to reset, or ask an admin to raise the limit.",
                "details": {
                    "resource": "uploads",
                    "current": 10,
                    "limit": 10,
                    "window": "day",
                    "resets_at": "2026-10-14T00:00:00Z",
                },
            }
        },
        request=httpx.Request("POST", "https://api.test/v1/uploads"),
    )
    error = error_from_response(response)
    assert isinstance(error, RateLimitError)
    assert error.fix == "Wait for the window to reset, or ask an admin to raise the limit."
    text = str(error)
    assert "10 of 10 uploads per day; resets 2026-10-14T00:00:00Z" in text
    assert "To fix: Wait for the window to reset" in text
