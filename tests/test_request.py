"""Request: an immutable value object over one parsed request line + headers + body.

Two pieces of logic live here, and both are there because more than one consumer needs
them and neither carries policy: the target split (`path` / `query`) and case-insensitive
header lookup (`header`). Everything else in this file pins *properties the rest of the
codebase leans on* rather than behaviour Request implements — that it cannot be mutated,
that equality is by value, and that path/query stay derived rather than stored.
"""

import dataclasses
import typing

import pytest

from request import Request


def make(target: str = "/", **overrides) -> Request:
    fields = dict(method="GET", target=target, headers={}, body=b"")
    fields.update(overrides)
    return Request(**fields)


# --------------------------------------------------------------------------------------
# The target split.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target, expected_path, expected_query",
    [
        ("/", "/", ""),
        ("/users", "/users", ""),
        ("/users?id=3", "/users", "id=3"),
        ("/users?id=3&x=a", "/users", "id=3&x=a"),
        # No '?' at all: query is the empty string, never None. One type for consumers.
        ("/a/b/c", "/a/b/c", ""),
        # Trailing '?' with nothing after it.
        ("/users?", "/users", ""),
        # Partition on the FIRST '?' — a query value may legally contain one.
        ("/search?q=a?b", "/search", "q=a?b"),
        ("/search?next=/x?y=1", "/search", "next=/x?y=1"),
        # '?' as the very first character: empty path, everything else is query.
        ("?x=1", "", "x=1"),
        # The path is NOT percent-decoded. Decoding before routing is how %2e%2e%2f
        # becomes path traversal; decoding is deferred to a week that can think about it.
        ("/%2e%2e/etc", "/%2e%2e/etc", ""),
        ("/a%20b?x=%20", "/a%20b", "x=%20"),
        # '#' has no meaning in a request target — fragments never reach the server.
        # Pinned so that "helpfully" stripping it later is a visible change.
        ("/page#frag", "/page#frag", ""),
    ],
)
def test_target_splits_into_path_and_query(target, expected_path, expected_query):
    request = make(target)
    assert request.path == expected_path
    assert request.query == expected_query


def test_path_and_query_recompose_into_target():
    """The split is lossless except for the separator itself."""
    for target in ["/users?id=3", "/users", "/a?b=c?d"]:
        request = make(target)
        separator = "?" if "?" in target else ""
        assert request.path + separator + request.query == target


def test_empty_query_and_absent_query_differ_in_target_only():
    """The reason `target` is kept rather than replaced by path + query."""
    with_marker, without_marker = make("/users?"), make("/users")

    assert with_marker.path == without_marker.path
    assert with_marker.query == without_marker.query
    assert with_marker.target != without_marker.target
    assert with_marker != without_marker


# --------------------------------------------------------------------------------------
# Case-insensitive header lookup.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stored, looked_up",
    [
        ("content-type", "content-type"),
        ("content-type", "Content-Type"),
        ("content-type", "CONTENT-TYPE"),
        # Works even when the producer did not normalise on insert. The parser does
        # lowercase on insert, but `header` does not depend on it.
        ("Content-Type", "content-type"),
        ("CoNtEnT-tYpE", "Content-Type"),
    ],
)
def test_header_lookup_ignores_case_on_both_sides(stored, looked_up):
    request = make(headers={stored: "text/plain"})
    assert request.header(looked_up) == "text/plain"


def test_header_returns_none_when_absent():
    assert make(headers={"host": "h"}).header("content-type") is None


def test_header_returns_the_supplied_default_when_absent():
    request = make(headers={"host": "h"})
    assert request.header("content-type", "application/octet-stream") == (
        "application/octet-stream"
    )


def test_empty_header_value_is_returned_not_replaced_by_the_default():
    """`""` is falsy. An implementation written as `headers.get(name) or default` passes
    every other test in this section and fails this one — a header that was genuinely
    sent empty would come back as the default, which is a different fact."""
    request = make(headers={"x-empty": ""})
    assert request.header("x-empty", "fallback") == ""


def test_header_lookup_does_not_match_on_prefix_or_substring():
    request = make(headers={"content-type": "text/plain"})
    assert request.header("content") is None
    assert request.header("type") is None
    assert request.header("x-content-type") is None


def test_headers_dict_remains_directly_accessible():
    """`header()` is the ergonomic path, not a wall. Parser-produced keys are lowercase,
    so direct indexing still works for code that knows the invariant."""
    request = make(headers={"host": "example.com"})
    assert request.headers["host"] == "example.com"


# --------------------------------------------------------------------------------------
# Known limits — pinned so a future change is deliberate rather than accidental.
# --------------------------------------------------------------------------------------


def test_absolute_form_target_is_not_split_into_host_and_path():
    """`GET http://h/p HTTP/1.1` is legal for proxies. shizuku treats the whole thing as
    a path, which is wrong for a proxy and fine for an origin server. Known limit."""
    assert make("http://example.com/p?q=1").path == "http://example.com/p"


def test_asterisk_form_target_is_passed_through():
    """`OPTIONS * HTTP/1.1`. Not special-cased; the router will simply 404 it."""
    assert make("*", method="OPTIONS").path == "*"


# --------------------------------------------------------------------------------------
# Properties the rest of the codebase depends on.
# --------------------------------------------------------------------------------------


def test_request_is_immutable():
    """Load-bearing: the parser hands one Request to the router, which hands it to a
    handler. A handler that could rewrite `target` would corrupt access logging and any
    later middleware — and nothing would point at the culprit."""
    request = make("/users")
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.target = "/hacked"


def test_derived_attributes_cannot_be_assigned():
    """path/query are computed from target and must not become a second source of truth."""
    request = make("/users?id=3")
    with pytest.raises(AttributeError):
        request.path = "/other"


def test_path_and_query_are_derived_not_stored():
    """If either becomes a real field it can drift from `target`. This catches that."""
    field_names = {f.name for f in dataclasses.fields(Request)}
    assert field_names == {"method", "target", "headers", "body"}


def test_equality_is_by_value():
    """The whole of test_parser.py asserts `parser.feed(...) == Request(...)`. That
    depends entirely on generated __eq__, and nothing else verifies it."""
    assert make("/users", headers={"host": "h"}, body=b"x") == make(
        "/users", headers={"host": "h"}, body=b"x"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"method": "POST"},
        {"target": "/other"},
        {"headers": {"host": "h"}},
        {"body": b"different"},
    ],
    ids=["method", "target", "headers", "body"],
)
def test_every_field_participates_in_equality(overrides):
    """A field excluded from __eq__ would make parser assertions silently weaker."""
    assert make() != make(**overrides)


def test_request_is_not_hashable_because_headers_is_a_dict():
    """Consequence of storing headers as a plain dict: a Request cannot be a dict key or
    go in a set. Pinned here so that if per-request caching ever needs it, the failure is
    an expected one with a known fix (a frozen mapping) rather than a surprise."""
    with pytest.raises(TypeError):
        hash(make("/users"))


def test_annotations_pin_the_text_above_bytes_below_boundary():
    """Unusual to assert on annotations, and justified here: this one is a decision, not
    documentation. The request line and header names/values are decoded to str at the
    parser boundary; the body stays arbitrary octets, because guessing a body's encoding
    is the application's job. Getting it wrong surfaces much later as b"/users" failing
    to match "/users" in a router dict.

    Dataclasses do not enforce annotations at runtime, so nothing else can catch this.
    """
    hints = typing.get_type_hints(Request)
    assert hints["method"] is str
    assert hints["target"] is str
    assert hints["body"] is bytes, "bodies are octets, not text"
    assert hints["headers"] is not str, "headers are a mapping, not a string"


def test_repr_includes_the_fields():
    """Failure output in the 35 parser tests is this repr. If it stops showing fields,
    every parser failure becomes unreadable."""
    text = repr(make("/users?id=3", method="POST", body=b"hi"))
    assert "POST" in text
    assert "/users?id=3" in text
    assert "hi" in text
