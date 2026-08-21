# b'GET / HTTP/1.1\r\nHost: example.com\r\n\r\n'
# b'POST /submit HTTP/1.1\r\nHost: h\r\nContent-Length: 5\r\n\r\nhello'
from request import Request


class MalformedRequest(Exception): ...


class ParserState:
    @staticmethod
    def feed(parser, data: bytes) -> Request | None:
        raise NotImplementedError()

    @staticmethod
    def _take_line(parser) -> bytes | None:
        try:
            terminator_ind = parser.buffer.index(b"\n")
        except ValueError:
            return None
        if terminator_ind == 0:
            raise MalformedRequest()
        line = parser.buffer[: terminator_ind - 1]
        if line == b"":
            return b""
        del parser.buffer[: terminator_ind + 1]
        return line


class StartLineState(ParserState):
    @staticmethod
    def feed(parser, data: bytes) -> None:
        parser.buffer += data
        line = ParserState._take_line(parser)
        if line is None:
            return None
        if len(line.split(b" ")) != 3:
            raise MalformedRequest()

        parser._method, parser._target, parser._version = [
            str(s, encoding="ascii") for s in line.split(b" ")
        ]
        parser.new_state(HeadersState)
        parser.feed(b"")
        return None


class HeadersState(ParserState):
    @staticmethod
    def feed(parser, data: bytes) -> None:
        parser.buffer += data
        line = ParserState._take_line(parser)
        if line is None:
            return None
        while line and len(line) > 0:
            try:
                k, v = line.split(b":")
                k = str(k, encoding="ascii").lower()
                v = str(v, encoding="ascii").strip()
                parser._headers[k] = v
                line = ParserState._take_line(parser)

            except ValueError:
                raise MalformedRequest

        if line is None:
            return None
        if line == b"":
            parser.new_state(BodyState)
            parser.feed(b"")
        return None


class BodyState(ParserState): ...


class FinalState(ParserState): ...


class RequestParser:
    def __init__(self):
        self.new_state(StartLineState)
        self.buffer: bytearray = bytearray()
        self.error = None
        self._method: str = ""
        self._target: str = ""
        self._version: str = ""
        self._body: bytes = b""
        self._headers: dict[str, str] = {}

    def new_state(self, newstate) -> None:
        self._state = newstate

    def _take_line(self):
        return self._state._take_line(self)

    def feed(self, data: bytes) -> Request | None:
        return self._state.feed(self, data)

    def __repr__(self):
        return (
            f"STATE: {str(self._state.__name__)}\n"
            f"  buffer: {self.buffer}\n"
            f"  method: {self._method} target: {self._target} version: {self._version}\n"
            f"  headers: {self._headers}\n"
        )


if __name__ == "__main__":
    parser = RequestParser()
    chunk1 = b"GET / "
    chunk2 = b"HT"
    chunk3 = b"TP/1.1\r\n"
    chunk4 = b"Host: "
    chunk5 = b"dtask.dev\r"
    chunk6 = b"\n"
    parser.feed(chunk1)
    parser.feed(chunk2)
    parser.feed(chunk3)
    parser.feed(chunk4)
    parser.feed(chunk5)
