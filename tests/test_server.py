"""Server layer: the pure part of section 4's three-outcome mapping.

Red until box 4 lands. It exists so the week 1 DoD line "malformed input -> 400" cannot
be ticked while nothing maps a parser error to a response.

DECISION — box 4 splits in two. `handle` is the mapping, and it is pure. The shell around
it (`asyncio.start_server`, the reads and the writes) is the only part that needs a
socket. Box 5's integration tests stay in their own file and test the shell.

Interface posited here:

    handle(parser, router, data: bytes) -> Response | None

    None      -> the parser wants more bytes; read again
    Response  -> a routed request, or a 400 for a parked parser error

DECISION — the 400 belongs to the server layer, not the router. The router never sees
parser state, and `dispatch` has no Request to take. Handler exceptions are already the
router's (d44aa2b), so this layer needs no try around dispatch.

A plain failing test, not xfail. `@pytest.mark.xfail(strict=True)` is the same guard with
the opposite default: green now, red when `handle` lands and XPASSes.
"""

from parser import RequestParser


def test_a_parked_parser_error_maps_to_a_bare_400():
    """Malformed bytes in, 400 out, with no Request in between.

    `b"GET /users\\r\\n\\r\\n"` has a two-field request line, so the parser parks with
    error 'request-line-two-fields'.
    """
    from router import Router
    from server import handle  # does not exist yet — this is the point

    parser = RequestParser()
    response = handle(parser, Router(), b"GET /users\r\n\r\n")

    assert parser.error is not None, "the parser must park before there is anything to map"
    assert response is not None, "a parked parser is terminal; None would mean read again"
    assert response.status == 400
    assert parser.error.encode() not in response.body, (
        "the reason string is for me, not for the client — notes section 2"
    )
