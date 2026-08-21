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

from parser import RequestParser
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
