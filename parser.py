from request import Request
import re


_VERSION = re.compile(r"^HTTP/\d\.\d$")


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
            raise MalformedRequest('bare-lf-is-rejected')
        if parser.buffer[terminator_ind-1] != 13:
            raise MalformedRequest('bare-lf-after-header-value')
        line = parser.buffer[: terminator_ind - 1]
        del parser.buffer[: terminator_ind + 1]
        if line == b"":
            return b""
        return line

    @staticmethod
    def _take_bytes(parser, n: int) -> bytes | None:
        if len(parser.buffer) < n:
            return None
        body = parser._body = parser.buffer[:n]
        del parser.buffer[:n]
        return body

class StartLineState(ParserState):
    @staticmethod
    def feed(parser, data: bytes) -> None:
        parser.buffer += data
        line = ParserState._take_line(parser)
        if line is None:
            return None
        
        line_fields = line.split(b' ')
        if len(line_fields) == 2:
            raise MalformedRequest('request-line-two-fields')
        elif len(line_fields) == 4:
            raise MalformedRequest('request-line-four-fields')
        elif len(line.split(b" ")) == 3:
            parser._method, parser._target, parser._version = [
                str(s, encoding="ascii") for s in line_fields
            ]
        if parser._method == '':
            raise MalformedRequest('empty-method')
        if not _VERSION.match(parser._version):
            raise MalformedRequest('unparseable-version')
        
        parser.new_state(HeadersState)
        return parser.feed(b"")


class HeadersState(ParserState):
    @staticmethod
    def feed(parser, data: bytes) -> None:
        parser.buffer += data
        line = ParserState._take_line(parser)
        if line is None:
            return None
        while line and len(line) > 0:
            try:
                colon_ind: int = line.index(b':')
                k = line[:colon_ind]
                if k != k.strip():
                    raise MalformedRequest('white-space-around-header-name')
                v = line[colon_ind + 1:]
                k = str(k, encoding="ascii").lower()
                v = str(v, encoding="ascii").strip()
                if k in parser._headers:
                    curr_val = parser._headers[k]
                    v = curr_val + ',' + v
                parser._headers[k] = v
                if 'content-length' in parser._headers:
                    content_length: int = 0
                    try:
                        content_length = int(parser._headers['content-length'])
                    except ValueError:
                        raise MalformedRequest('Content-Length should be number')
                    if content_length < 0:
                        raise MalformedRequest('Content-Length should be > 0')
                line = ParserState._take_line(parser)

            except ValueError:
                raise MalformedRequest('Incorrect header format')

        if line is None:
            return None
        if line == b"":
            parser.new_state(BodyState)
            return parser.feed(b"")
        return None


class BodyState(ParserState):
    @staticmethod
    def feed(parser, data: bytes) -> None:
        parser.buffer += data
        body = ParserState._take_bytes(
            parser,
            int(parser._headers.get('content-length', 0))
        )
        if body is None:
            return None
        parser._body = body
        parser.new_state(FinalState)
        return parser.feed(b"")


class FinalState(ParserState):
    @staticmethod
    def feed(parser, data: bytes) -> None | Request:
        request = Request(
            method = parser._method,
            target = parser._target,
            headers = parser._headers,
            body = bytes(parser._body)
        )
        parser._method = ''
        parser._target = ''
        parser._version = ''
        parser._headers = {}
        parser._body = b''
        parser.new_state(StartLineState)
        return request

class ErrorState(ParserState):
    @staticmethod
    def feed(parser, data: bytes) -> None | Request:
        return None

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
        request: Request | None = None
        try:
            request = self._state.feed(self, data)
        except MalformedRequest as e:
            self.buffer = bytearray()
            if self.error is None:
                self.error = str(e)
            self.new_state(ErrorState)
            return None
        return request

    def __repr__(self):
        return (
            f"STATE: {str(self._state.__name__)}\n"
            f"  buffer: {self.buffer}\n"
            f"    method: {self._method}\n" 
            f"    target: {self._target}\n"
            f"    version: {self._version}\n"
            f"  headers: {self._headers}\n"
            f"  body: {str(self._body, encoding='ascii')}"
        )
