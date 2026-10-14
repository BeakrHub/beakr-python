"""The configured SDK read timeout actually bounds a stalled HTTP response.

SPEC-440: request construction alone cannot prove the HTTP client enforces it.
A loopback server accepts the request but withholds its response until cleanup.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

import httpx
import pytest

from beakr_sdk import APIConnectionError, AsyncBeakr, Beakr


@pytest.fixture
def stalled_server():
    release = Event()
    received = Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.set()
            release.wait(5)
            self.close_connection = True

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_sync_configured_timeout_refuses_stalled_response(stalled_server):
    base, received = stalled_server
    with Beakr(api_key="bk_test_timeout", base_url=base, timeout=0.15, max_retries=0) as client:
        with pytest.raises(APIConnectionError) as caught:
            client.me.get()
    assert received.is_set()
    assert isinstance(caught.value.__cause__, httpx.ReadTimeout)


async def test_async_configured_timeout_refuses_stalled_response(stalled_server):
    base, received = stalled_server
    async with AsyncBeakr(
        api_key="bk_test_timeout", base_url=base, timeout=0.15, max_retries=0
    ) as client:
        with pytest.raises(APIConnectionError) as caught:
            await client.me.get()
    assert received.is_set()
    assert isinstance(caught.value.__cause__, httpx.ReadTimeout)
