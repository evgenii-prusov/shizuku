"""RequestParser: sans-IO HTTP/1.1 request parsing.

Nothing in this file opens a socket, and nothing in `parser.py` may import one.
Two greps are part of the contract:

    grep -n socket parser.py      -> empty
    grep -n response parser.py    -> empty

The second matters as much as the first: the parser exposes an error *state* with a reason string for me, and the server layer maps that to a 400. A parser that can build a Response can leak its internal reason to the client.

Interface pinned here:

    parser = RequestParser()
    parser.feed(data: bytes) -> Request | None    # None while incomplete
    parser.error -> str | None                    # None until ERROR, then sticky
"""

import pytest

from parser import RequestParser, MAX_HEADER_BYTES
from request import Request


def parse_all(raw: bytes):
    """Feed the whole buffer in one call."""
    parser = RequestParser()
    return parser, parser.feed(raw)


# Valid requests. Each `raw` below is EXACTLY one complete request with nothing trailing, which is what lets the byte-at-a-time replay reuse this table.

COMPLETE = [
    (
        "get-no-body",
        b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n",
        Request("GET", "/", {"host": "example.com"}, b""),
    ),
    (
        "get-with-query",
        b"GET /users?id=3&x=a HTTP/1.1\r\nHost: h\r\n\r\n",
        Request("GET", "/users?id=3&x=a", {"host": "h"}, b""),
    ),
    (
        "post-with-content-length",
        b"POST /submit HTTP/1.1\r\nHost: h\r\nContent-Length: 5\r\n\r\nhello",
        Request("POST", "/submit", {"host": "h", "content-length": "5"}, b"hello"),
    ),
    (
        "mixed-case-header-names-are-lowercased-on-insert",
        b"GET / HTTP/1.1\r\nHOST: h\r\nCoNtEnT-tYpE: text/plain\r\n\r\n",
        Request("GET", "/", {"host": "h", "content-type": "text/plain"}, b""),
    ),
    (
        # DECISION: repeated headers comma-join, per RFC 9110 field-order semantics.
        # Change to keep-first if you prefer — but change it here first, deliberately.
        "duplicate-header-comma-joins",
        b"GET / HTTP/1.1\r\nX-Tag: a\r\nX-Tag: b\r\n\r\n",
        Request("GET", "/", {"x-tag": "a,b"}, b""),
    ),
    (
        "empty-header-value",
        b"GET / HTTP/1.1\r\nX-Empty:\r\n\r\n",
        Request("GET", "/", {"x-empty": ""}, b""),
    ),
    (
        # The parser does not police method names — that is the router's 404.
        # Framing is the parser's only job.
        "unknown-method-is-not-a-parse-error",
        b"FROB / HTTP/1.1\r\nHost: h\r\n\r\n",
        Request("FROB", "/", {"host": "h"}, b""),
    ),
    (
        "zero-length-body-post",
        b"POST /x HTTP/1.1\r\nContent-Length: 0\r\n\r\n",
        Request("POST", "/x", {"content-length": "0"}, b""),
    ),
    (
        # DECISION: no Content-Length on a POST means a zero-length body, not an error.
        "post-without-content-length-has-empty-body",
        b"POST /x HTTP/1.1\r\nHost: h\r\n\r\n",
        Request("POST", "/x", {"host": "h"}, b""),
    ),
    (
        "header-value-containing-colons",
        b"GET / HTTP/1.1\r\nX-Time: 10:30:00\r\n\r\n",
        Request("GET", "/", {"x-time": "10:30:00"}, b""),
    ),
]

COMPLETE_IDS = [case[0] for case in COMPLETE]
COMPLETE_ARGS = [(case[1], case[2]) for case in COMPLETE]


@pytest.mark.parametrize("raw, expected", COMPLETE_ARGS, ids=COMPLETE_IDS)
def test_complete_request_parses(raw, expected):
    parser, request = parse_all(raw)
    assert parser.error is None
    assert request == expected


@pytest.mark.parametrize("raw, expected", COMPLETE_ARGS, ids=COMPLETE_IDS)
def test_request_split_across_every_byte_boundary(raw, expected):
    """The chunk-boundary property, proven mechanically.

    Two assertions, and the second is the one that matters: every prefix must return
    None. Without it this test also passes on a parser that merely got lucky about
    where the chunk boundaries happened to land.
    """
    parser = RequestParser()
    results = [parser.feed(raw[i : i + 1]) for i in range(len(raw))]

    assert results[-1] == expected, "the final byte must complete the request"
    assert all(r is None for r in results[:-1]), "no prefix may produce a Request"
    assert parser.error is None


# --------------------------------------------------------------------------------------
# Incomplete: returns None and stays clean. Not an error — more bytes may still arrive.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"", id="nothing-yet"),
        pytest.param(b"GET / HTT", id="partial-request-line"),
        pytest.param(b"GET / HTTP/1.1\r\n", id="request-line-only"),
        pytest.param(b"GET / HTTP/1.1\r\nHost: h\r\n", id="missing-final-crlf"),
        pytest.param(
            b"POST /x HTTP/1.1\r\nContent-Length: 10\r\n\r\nshort",
            id="body-shorter-than-declared",
        ),
    ],
)
def test_incomplete_returns_none_without_error(raw):
    parser, request = parse_all(raw)
    assert request is None
    assert parser.error is None


# --------------------------------------------------------------------------------------
# Malformed: sticky error, which the server layer maps to a bare 400.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"GET /\r\n\r\n", id="request-line-two-fields"),
        pytest.param(b"GET / HTTP/1.1 extra\r\n\r\n", id="request-line-four-fields"),
        pytest.param(b" / HTTP/1.1\r\n\r\n", id="empty-method"),
        pytest.param(b"GET / HTTP/9\r\n\r\n", id="unparseable-version"),
        pytest.param(
            b"GET / HTTP/1.1\r\nContent-Length: abc\r\n\r\n",
            id="non-integer-content-length",
        ),
        pytest.param(
            b"GET / HTTP/1.1\r\nContent-Length: -5\r\n\r\n",
            id="negative-content-length",
        ),
        pytest.param(
            b"GET / HTTP/1.1\r\nNoColonHere\r\n\r\n", id="header-without-colon"
        ),
        pytest.param(b"GET / HTTP/1.1\nHost: h\n\n", id="bare-lf-is-rejected"),
    ],
)
def test_malformed_sets_an_error(raw):
    parser, request = parse_all(raw)
    assert request is None
    assert parser.error, "ERROR state must carry a reason — for me, not for the client"
    assert isinstance(parser.error, str)


def test_error_is_sticky():
    """Once 400 has been decided, no later bytes can rescue the connection."""
    parser = RequestParser()
    parser.feed(b"GET /\r\n\r\n")
    first_error = parser.error
    assert first_error

    assert parser.feed(b"GET / HTTP/1.1\r\nHost: h\r\n\r\n") is None
    assert parser.error == first_error


# --------------------------------------------------------------------------------------
# Pipelining. Kept out of the replay table above because the request completes before
# the final byte, which is precisely the property this asserts.
# --------------------------------------------------------------------------------------


def test_bytes_past_content_length_do_not_corrupt_the_body():
    """Trailing bytes belong to the NEXT request. Even with no keep-alive yet, the body
    must be exactly Content-Length and the parser must not choke."""
    parser = RequestParser()
    request = parser.feed(
        b"POST /x HTTP/1.1\r\nContent-Length: 5\r\n\r\nhelloGET /next HTTP/1.1\r\n\r\n"
    )
    assert parser.error is None
    assert request == Request("POST", "/x", {"content-length": "5"}, b"hello")


# --------------------------------------------------------------------------------------
# Regression tests added 2026-08-22 after a review found the two hardest cases above
# passing for reasons other than the property they claim. Each test below fails against
# the parser as written; that is the point of adding them.
# --------------------------------------------------------------------------------------


# `_take_line` slices `buffer[:terminator_ind - 1]`, which assumes a CR sits before the
# LF without checking. On a bare LF it eats the last real byte instead of rejecting.
#
# The existing `bare-lf-is-rejected` case hides this: it puts the LF on the *request
# line*, where truncation turns `HTTP/1.1` into `HTTP/1.` and the version regex errors
# anyway. The error is real, the reason is not the one being claimed. Every case below
# puts the LF somewhere the truncation is silent.


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(
            b"GET / HTTP/1.1\r\nHost: example\nX-A: 1\r\n\r\n",
            id="bare-lf-after-header-value",
        ),
        pytest.param(
            b"GET / HTTP/1.1\r\nX-A: 1\r\nHost: example\n\r\n",
            id="bare-lf-on-the-last-header",
        ),
    ],
)
def test_bare_lf_in_a_header_line_is_an_error_not_a_truncation(raw):
    """Currently returns a Request carrying `{'host': 'exampl'}` — the final byte of the
    value silently gone, no error set. A corrupted header is worse than a 400."""
    parser, request = parse_all(raw)
    assert request is None, "a bare LF must not yield a Request"
    assert parser.error


def test_bare_lf_cannot_shorten_content_length():
    """The same one-byte truncation applied to framing, which is where it stops being a
    cosmetic bug: `Content-Length: 10` becomes `1`, the body is cut to a single byte, and
    the remaining nine bytes are left to be read as the start of a second request. That
    shape is request smuggling."""
    parser, request = parse_all(
        b"POST /x HTTP/1.1\r\nContent-Length: 10\nHost: h\r\n\r\n0123456789"
    )
    assert parser.error, "bare LF in the Content-Length line must be rejected"
    assert request is None


def test_a_completed_parser_does_not_re_emit_its_request():
    """There is no DONE state: `FinalState.feed` rebuilds the same Request on every call,
    so a keep-alive read loop serves request #1's response forever.

    This asserts only the design-agnostic half — request #1 must not come back. What
    *should* happen after completion is an open decision (one parser instance per
    request, or an explicit `reset()`); both satisfy this test, `feed` returning the
    stale Request satisfies neither.
    """
    parser = RequestParser()
    first = parser.feed(b"GET /one HTTP/1.1\r\nHost: h\r\n\r\n")
    assert first == Request("GET", "/one", {"host": "h"}, b"")

    assert parser.feed(b"") is not first
    assert parser.feed(b"POST /two HTTP/1.1\r\nHost: h\r\n\r\n") != first


def test_parsing_writes_nothing_to_stdout(capsys):
    """`HeadersState` still carries three `print()` calls from debugging. pytest captures
    them, which is exactly why the suite never noticed; under a real server they are
    per-header noise on the process's stdout."""
    RequestParser().feed(b"GET / HTTP/1.1\r\nHost: h\r\nX-Tag: a\r\nX-Tag: b\r\n\r\n")
    assert capsys.readouterr().out == ""


def test_whitespace_before_the_colon_is_rejected():
    parser, request = parse_all(b"GET / HTTP/1.1\r\nHost : h\r\n\r\n")
    assert request is None
    assert parser.error


def test_single_unterminated_line_past_the_limit_is_rejected():
    parser, request = parse_all(b'A' * (MAX_HEADER_BYTES + 1))
    assert request is None
    assert parser.error


def test_bytes_at_the_limit_boundary():
    parser, request = parse_all(b'A' * MAX_HEADER_BYTES)
    assert request is None
    assert parser.error is None


def test_many_small_complete_headers_cumulatively_exceed_the_limit():
    raw_msg = b'GET /ping HTTP/1.1\r\n'
    headers = []
    for i in range(1000):
        headers.append(f'X-{i}: {i}')
    headers = b'\r\n'.join(bytes(header, encoding='ascii') for header in headers)
    final_msg = raw_msg + headers
    parser, request = parse_all(final_msg)
    assert request is None
    assert parser.error


def test_many_small_headers_trickled_across_feed_calls_still_trips_the_limit():
    """Same property as the test above, but each header line arrives in its own feed()
    call instead of one big blob. _take_line deletes a line from parser.buffer as soon as
    it's consumed, so a limit checked only against a len(parser.buffer) snapshot never
    sees more than one small line at a time here — the blob version above only trips
    because the whole thing lands in the buffer before any line is consumed."""
    parser = RequestParser()
    assert parser.feed(b'GET /ping HTTP/1.1\r\n') is None
    assert parser.error is None

    sent = 0
    request = None
    i = 0
    while parser.error is None and sent <= MAX_HEADER_BYTES:
        line = f'X-{i}: {i}\r\n'.encode('ascii')
        request = parser.feed(line)
        sent += len(line)
        i += 1

    assert request is None
    assert parser.error


def test_header_byte_count_resets_between_requests():
    """A first request that lands right under the limit must not leave enough of the
    count behind to falsely trip an unrelated second request on the same connection."""
    parser = RequestParser()

    request_line = b'GET / HTTP/1.1\r\n'
    header_prefix = b'X-Pad: '
    header_suffix = b'\r\n\r\n'
    overhead = len(request_line) + len(header_prefix) + len(header_suffix)
    pad_value = b'a' * (MAX_HEADER_BYTES - overhead - 1)
    first_raw = request_line + header_prefix + pad_value + header_suffix

    first = parser.feed(first_raw)
    assert parser.error is None
    assert first == Request('GET', '/', {'x-pad': pad_value.decode('ascii')}, b'')

    second_raw = b'GET /two HTTP/1.1\r\nHost: h\r\n\r\n'
    second = parser.feed(second_raw)
    assert parser.error is None
    assert second == Request('GET', '/two', {'host': 'h'}, b'')
