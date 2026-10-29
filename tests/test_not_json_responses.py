"""A response that is not JSON never becomes the error message as it is.

Root cause: ``_typed_error`` fell back to ``response.text`` for the message, so
a client pointed at a wrong base URL (a web app, a proxy, a server that is not
Beakr) raised the whole HTML page as the error, and the CLI printed it. A 200
HTML page (a web app answers 200 for any path) reached ``response.json()`` and
raised a bare ``JSONDecodeError``.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from beakr_sdk import Beakr, BeakrError, NotFoundError, ServerError, UnexpectedResponseError
from beakr_sdk.errors import error_from_response

API = "https://api.test"

_PAGE = """<!DOCTYPE HTML>
<html lang="en">
    <head>
        <meta charset="utf-8">
        <title>Error response</title>
    </head>
    <body>
        <h1>Error response</h1>
        <p>Error code: 404</p>
        <p>Message: File not found.</p>
    </body>
</html>
"""


def _html(status: int, page: str = _PAGE) -> httpx.Response:
    request = httpx.Request("GET", "https://wrong.test/v1/projects?limit=5")
    return httpx.Response(status, text=page, headers={"content-type": "text/html"}, request=request)


def test_an_html_error_page_is_named_by_address_and_title_not_printed() -> None:
    error = error_from_response(_html(404))

    assert isinstance(error, NotFoundError)
    text = str(error)
    assert "<html" not in text and "<!DOCTYPE" not in text
    assert error.message == (
        'https://wrong.test/v1/projects answered with an HTML page ("Error response"), '
        "not a Beakr API response."
    )
    assert "BEAKR_BASE_URL" in (error.fix or "")
    assert "(HTTP 404, not_found)" in text
    # The page is still there for a caller that wants it.
    assert error.body == _PAGE


def test_an_html_gateway_page_says_to_retry_when_the_url_is_right() -> None:
    error = error_from_response(_html(502, "<html><title>502 Bad Gateway</title></html>"))

    assert isinstance(error, ServerError)
    assert '("502 Bad Gateway")' in error.message
    assert "retry shortly" in (error.fix or "")


def test_an_html_page_without_a_content_type_is_still_recognised() -> None:
    request = httpx.Request("GET", "https://wrong.test/v1/me")
    response = httpx.Response(404, content=b"<html><body>nope</body></html>", request=request)

    error = error_from_response(response)

    assert (
        error.message
        == "https://wrong.test/v1/me answered with an HTML page, not a Beakr API response."
    )


def test_plain_text_bodies_are_cut_to_one_short_line() -> None:
    request = httpx.Request("GET", "https://api.test/v1/me")
    long_text = "upstream connect error\n" + "x" * 1000
    error = error_from_response(httpx.Response(503, text=long_text, request=request))

    assert error.message.startswith("upstream connect error x")
    assert len(error.message) <= 200 and error.message.endswith("...")
    assert "\n" not in error.message


@respx.mock
def test_a_200_html_page_raises_a_typed_error_not_a_json_decode_error() -> None:
    respx.get(f"{API}/v1/projects").respond(
        200,
        text="<html><head><title>Beakr</title></head></html>",
        headers={"content-type": "text/html"},
    )

    with Beakr(api_key="bk_live_test", base_url=API) as beakr:
        with pytest.raises(UnexpectedResponseError) as raised:
            beakr.projects.list()

    error = raised.value
    assert isinstance(error, BeakrError)
    assert error.status == 200 and error.code == "unexpected_response"
    assert error.message.startswith(f'{API}/v1/projects answered with an HTML page ("Beakr")')
    assert error.response is not None


@respx.mock
def test_the_cli_and_scripts_see_the_same_message_through_the_client() -> None:
    respx.get(f"{API}/v1/projects").mock(
        return_value=httpx.Response(404, text=_PAGE, headers={"content-type": "text/html"})
    )

    with Beakr(api_key="bk_live_test", base_url=API) as beakr:
        with pytest.raises(NotFoundError) as raised:
            beakr.projects.list()

    assert "<html" not in str(raised.value)
    assert f"{API}/v1/projects answered with an HTML page" in str(raised.value)
