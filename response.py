from http import HTTPStatus
from dataclasses import dataclass


@dataclass
class Response:
    status_code: int
    headers: dict[str, str]
    body: bytes

    def serialize(self) -> bytes:
        status_line = (
            f"HTTP/1.1 {self.status_code} {HTTPStatus(self.status_code).phrase}\r\n"
        )
        header = bytes(status_line, encoding="ascii")

        headers = {k: v for k, v in self.headers.items() if k.lower() != "content-length"}
        headers["Content-Length"] = str(len(self.body))
        headers_lines = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        header = bytes(status_line + headers_lines + "\r\n", encoding="ascii")

        return header + self.body
