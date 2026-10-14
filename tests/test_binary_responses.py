"""Binary downloads come back as bytes, never decoded as JSON.

Root cause guarded: ``files.preview_pdf`` was a JSON call, so a PDF raised a JSON
decode error; generated clients decoded zips the same way because the public
contract advertised JSON for binary routes.
"""

from __future__ import annotations

import httpx
import respx

from beakr_sdk import Beakr

API = "https://api.test"
PDF = b"%PDF-1.7\x00\xff\xfe binary"


@respx.mock
def test_a_json_call_that_receives_a_file_returns_its_bytes() -> None:
    respx.get(f"{API}/v1/me").mock(
        return_value=httpx.Response(200, content=PDF, headers={"content-type": "application/zip"})
    )
    with Beakr(api_key="bk_live_test", base_url=API) as client:
        assert client.me.get() == PDF
