"""Router: exact (method, path) dispatch.

DECISION encoded here — the router, not the server layer, catches handler exceptions
and returns a 500. That makes the contract total (`Request -> Response`, always) and is
the only arrangement where the DoD's "handler exception -> 500" can be proven without a
socket. The server layer keeps a `try` as a backstop, but it should never fire.

DECISION — method mismatch on a known path returns 404, not 405. Strictly it is 405 with
an `Allow` header; the week 1 DoD says unknown path -> 404 and is silent on mismatch, so
404 is the on-spec answer. Change it deliberately, not by accident.
"""

import pytest

from request import Request
from response import Response
from router import Router


def get(target: str = "/", method: str = "GET") -> Request:
    return Request(method=method, target=target, headers={}, body=b"")


@pytest.fixture
def router():
    router = Router()
    router.register("GET", "/", lambda request: Response(200, {}, b"root"))
    router.register("GET", "/users", lambda request: Response(200, {}, b"users"))
    router.register("POST", "/users", lambda request: Response(200, {}, b"created"))
    return router


def test_dispatches_to_the_registered_handler(router):
    assert router.dispatch(get("/users")).body == b"users"


def test_dispatch_discriminates_on_method(router):
    assert router.dispatch(get("/users", method="GET")).body == b"users"
    assert router.dispatch(get("/users", method="POST")).body == b"created"


def test_unknown_path_is_404(router):
    assert router.dispatch(get("/nope")).status == 404


def test_known_path_wrong_method_is_404(router):
    """See the module docstring: 404 this week, 405 when Allow can be built properly."""
    assert router.dispatch(get("/users", method="DELETE")).status == 404


def test_dispatch_matches_on_path_not_target(router):
    """The query string must not defeat routing — this is why Request splits the target."""
    assert router.dispatch(get("/users?id=3")).status == 200
    assert router.dispatch(get("/users?id=3")).body == b"users"


def test_match_is_exact_not_prefix(router):
    assert router.dispatch(get("/users/1")).status == 404
    assert router.dispatch(get("/user")).status == 404


def test_handler_exception_becomes_500(router):
    def explode(request):
        raise RuntimeError("boom")

    router.register("GET", "/boom", explode)
    assert router.dispatch(get("/boom")).status == 500


def test_500_body_does_not_leak_the_exception(router):
    """Same category of mistake as the parser returning a Response: internal detail
    crossing a boundary it has no business crossing. Log the traceback, send the status."""

    def explode(request):
        raise RuntimeError("SECRET-a1b2c3")

    router.register("GET", "/boom", explode)
    body = router.dispatch(get("/boom")).body
    assert b"SECRET-a1b2c3" not in body
    assert b"RuntimeError" not in body
    assert b"Traceback" not in body


def test_dispatch_always_returns_a_response(router):
    """Total contract: every branch — hit, miss, explosion — yields a Response."""
    for request in [get("/"), get("/nope"), get("/users", method="DELETE")]:
        assert isinstance(router.dispatch(request), Response)


def test_handler_receives_the_request(router):
    seen = []
    router.register(
        "GET", "/echo", lambda request: seen.append(request) or Response(200, {}, b"")
    )

    request = get("/echo")
    router.dispatch(request)
    assert seen == [request]
