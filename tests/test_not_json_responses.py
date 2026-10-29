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


def test_the_address_never_carries_a_user_name_password_or_query() -> None:
    # Security review: copy_with(query=None) kept userinfo, so a base URL with
    # credentials in it (https://user:pass@host) printed them in the message.
    request = httpx.Request("GET", "https://ada:s3cret@wrong.test:8443/v1/me?token=abc")
    response = httpx.Response(
        404, text=_PAGE, headers={"content-type": "text/html"}, request=request
    )

    error = error_from_response(response)

    assert error.message.startswith("https://wrong.test:8443/v1/me answered")
    assert "s3cret" not in str(error) and "ada" not in str(error) and "token" not in str(error)


def test_control_characters_in_a_title_or_excerpt_are_removed() -> None:
    # Security review: the title and excerpt are the server's text printed to a
    # terminal; an ANSI escape sequence in them must not reach it.
    page = "<html><title>Hi\x1b[2J\x1b]0;owned\x07 &#27;[31mthere\u202e</title></html>"
    error = error_from_response(_html(404, page))
    assert "\x1b" not in error.message and "\x07" not in error.message
    assert "\u202e" not in error.message
    assert '("Hi[2J]0;owned [31mthere")' in error.message

    request = httpx.Request("GET", "https://api.test/v1/me")
    plain = error_from_response(httpx.Response(503, text="down\x1b[31m now", request=request))
    assert plain.message == "down[31m now"


def test_the_title_search_reads_only_the_start_of_a_huge_body() -> None:
    # Security review: the unbounded DOTALL pattern scanned the whole body. A
    # title past the first 4096 characters is not looked for, and a huge
    # body with an unterminated title is cheap.
    late = "<html>" + " " * 5000 + "<title>Late</title></html>"
    assert error_from_response(_html(404, late)).message.endswith(
        "answered with an HTML page, not a Beakr API response."
    )

    huge = "<html><title>" + "x" * 5_000_000
    error = error_from_response(_html(404, huge))
    assert "x" * 100 not in error.message
