"""Response: serialization to bytes.

This is the only place in the codebase that writes \\r\\n, which is why it is worth a
literal byte-string assertion rather than a structural one.
"""
import pytest

from response import Response


def split_message(raw: bytes):
    """-> (status_line, {header: value}, body). Used where asserting on a literal
    would pin header *order*, which is not part of the contract."""
    head, _, body = raw.partition(b"\r\n\r\n")
    status_line, *header_lines = head.split(b"\r\n")
    headers = {}
    for line in header_lines:
        name, _, value = line.partition(b": ")
        headers[name.decode().lower()] = value.decode()
    return status_line, headers, body


def test_minimal_response_serializes_to_an_exact_byte_string():
    assert Response(200, {}, b"hi").serialize() == (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Length: 2\r\n"
        b"\r\n"
        b"hi"
    )


@pytest.mark.parametrize(
    "status, reason",
    [
        (200, "OK"),
        (400, "Bad Request"),
        (404, "Not Found"),
        (500, "Internal Server Error"),
    ],
)
def test_reason_phrase_comes_from_the_status_code(status, reason):
    status_line, _, _ = split_message(Response(status, {}, b"").serialize())
    assert status_line == f"HTTP/1.1 {status} {reason}".encode()


@pytest.mark.parametrize("body", [b"", b"hi", b"x" * 1024, b"\xff\xfe\x00"])
def test_content_length_is_computed_from_the_body(body):
    _, headers, serialized_body = split_message(Response(200, {}, body).serialize())
    assert headers["content-length"] == str(len(body))
    assert serialized_body == body


def test_computed_content_length_overrides_a_hand_set_one():
    """A hand-set length that disagrees with the body is what makes curl hang forever
    waiting for bytes that never arrive. The serializer is the single source of truth."""
    _, headers, body = split_message(
        Response(200, {"Content-Length": "999"}, b"hi").serialize()
    )
    assert headers["content-length"] == "2"
    assert body == b"hi"


def test_custom_headers_are_emitted():
    _, headers, _ = split_message(
        Response(200, {"Content-Type": "text/plain"}, b"hi").serialize()
    )
    assert headers["content-type"] == "text/plain"


def test_empty_body_still_sends_content_length_zero():
    """Without this a client cannot tell 'no body' from 'body not sent yet'."""
    _, headers, body = split_message(Response(404, {}, b"").serialize())
    assert headers["content-length"] == "0"
    assert body == b""


def test_headers_are_separated_from_body_by_a_blank_line():
    raw = Response(200, {"X-A": "1"}, b"body").serialize()
    assert raw.count(b"\r\n\r\n") == 1
    assert raw.endswith(b"\r\n\r\nbody")
