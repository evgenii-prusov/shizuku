"""Box 5 — the server layer over a real socket and a real event loop.

Separate file from `test_parser.py` on purpose. That separation is what the week 1 DoD
box is checking: everything provable without a socket is proven without one, and only
what genuinely needs a connection lives here.

DECISION — no `pytest-asyncio`. It is not in `pyproject.toml` and it is not needed: a
plain sync test can call `asyncio.run` on an async body. One less dev dependency.

DECISION — port 0. The OS picks a free port and `server.sockets[0].getsockname()[1]`
reads it back, so a `server.py` still running on 25000 cannot collide.

DECISION — every test body runs under `asyncio.timeout`. The failure mode these tests
guard is a hang, and an unbounded one blocks the whole suite instead of failing once.

Each test states the mutant it kills, because an integration test that passes against a
broken server is worse than no test:

  1. a server that drops the handler's body or miscounts `Content-Length`
  2. a server that answers before `Content-Length` bytes have arrived
  3. a server that keeps reading after parking its parser

COUPLING — these tests use the routes registered at import in `server.py`
(`GET /ping`, `POST /users`). That router is module-level. If it ever becomes a
parameter, only `_running_server` changes.
"""

import asyncio
import contextlib

from server import handle_request

TIMEOUT = 3  # whole-test ceiling
QUIET = 0.3  # how long "the server has not answered yet" is observed for


@contextlib.asynccontextmanager
async def _running_server():
    """Deliberately `close()` without `wait_closed()`.

    `async with server` awaits `wait_closed()`, and since 3.12 that waits for every
    connection handler to finish. A handler stuck in `read()` — exactly the bug these
    tests exist to catch — makes that await hang forever, which swallows the
    `asyncio.timeout` below and turns a failing test into a frozen suite.
    `asyncio.run` cancels the leftover handler tasks at loop shutdown.
    """
    server = await asyncio.start_server(handle_request, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()


def _run(coro):
    async def guarded():
        async with asyncio.timeout(TIMEOUT):
            return await coro

    return asyncio.run(guarded())


def _split(raw: bytes) -> tuple[bytes, list[bytes], bytes]:
    """(status line, header lines, body).

    Split rather than substring-matched: `partition` eats the blank line, so the last
    header arrives with no trailing CRLF and `b"Content-Length: 6\\r\\n" in head` is
    false whenever the length header happens to come last.
    """
    head, _, body = raw.partition(b"\r\n\r\n")
    status, *headers = head.split(b"\r\n")
    return status, headers, body


def _header(headers: list[bytes], name: bytes) -> bytes | None:
    for line in headers:
        key, _, value = line.partition(b":")
        if key.strip().lower() == name.lower():
            return value.strip()
    return None


def test_a_get_reaches_the_handler_and_its_body_reaches_the_client():
    """Kills: a server that drops the body, or sends a length that disagrees with it.

    The `Content-Length` assertion is against `len(body)` rather than a literal `6`, so
    it holds whatever `/ping` is changed to return. A hand-set length that disagrees
    with the body is the bug that makes a real client hang forever.
    """

    async def exchange():
        async with _running_server() as port:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /ping HTTP/1.1\r\nHost: x\r\n\r\n")
            await writer.drain()
            writer.write_eof()
            raw = await reader.read()
            writer.close()
            await writer.wait_closed()
            return raw

    status, headers, body = _split(_run(exchange()))

    assert status == b"HTTP/1.1 200 OK"
    assert _header(headers, b"content-length") == str(len(body)).encode()
    assert body == b"online"


def test_the_response_is_withheld_until_content_length_bytes_arrive():
    """Kills: a server that answers on the headers alone.

    The headers go out in one TCP segment and the body in another, with a pause
    between. Sending both in a single write — as the first version of this test did —
    cannot tell a server that waits for the body from one that never needed it: both
    return 200. The pause is the entire test.
    """

    async def exchange():
        async with _running_server() as port:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"POST /users HTTP/1.1\r\nHost: x\r\nContent-Length: 5\r\n\r\n")
            await writer.drain()

            try:
                early = await asyncio.wait_for(reader.read(4096), QUIET)
            except TimeoutError:
                early = b""

            writer.write(b"hello")
            await writer.drain()
            writer.write_eof()
            late = await reader.read()
            writer.close()
            await writer.wait_closed()
            return early, late

    early, late = _run(exchange())

    assert early == b"", "answered before the declared body arrived"

    status, _headers, body = _split(late)
    assert status == b"HTTP/1.1 200 OK"
    assert body == b""


def test_a_parked_parser_answers_once_and_the_server_hangs_up():
    """Kills: a server that writes the 400 and then keeps reading.

    Two requests in one write: a malformed one, then a valid one. A correct server
    parks on the first, answers 400, and closes without ever looking at the second.

    The client deliberately does **not** half-close, so reaching EOF can only be the
    server hanging up. The earlier version called `write_eof()` first, which made the
    close happen either way — that assertion passed against a server with no `break`
    at all, which is to say it asserted nothing.
    """

    async def exchange():
        async with _running_server() as port:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /users\r\n\r\nGET /ping HTTP/1.1\r\nHost: x\r\n\r\n")
            await writer.drain()
            raw = await reader.read()
            at_eof = reader.at_eof()
            writer.close()
            await writer.wait_closed()
            return raw, at_eof

    raw, at_eof = _run(exchange())

    assert raw.count(b"HTTP/1.1") == 1, "a parked parser is terminal — answer once"

    status, _headers, body = _split(raw)
    assert status == b"HTTP/1.1 400 Bad Request"
    assert body == b"", "the parser's reason string is for me, not for the client"
    assert at_eof, "the server must close; the client never half-closed"
