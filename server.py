import asyncio
from asyncio import StreamReader, StreamWriter, start_server

from parser import RequestParser
from request import Request
from response import Response
from router import Router

router = Router()
router.register("GET", "/ping", lambda r: Response(200, {}, b"online"))
router.register("POST", "/users", lambda r: Response(200, {"Lol": "Kek"}, b""))


def handle(parser: RequestParser, router: Router, data: bytes) -> Response | None:
    request: Request | None = parser.feed(data)
    if request is None:
        if parser.error:
            return Response(400, {}, b"")
        return None

    response = router.dispatch(request)
    return response


async def handle_request(reader: StreamReader, writer: StreamWriter) -> None:
    parser: RequestParser = RequestParser()
    response: Response | None
    while True:
        data = await reader.read(1024)
        if not data:
            break
        response = handle(parser, router, data)
        if response:
            writer.write(response.serialize())
            await writer.drain()
            if response.status == 400:
                break

    writer.close()


async def main():
    server = await start_server(handle_request, "127.0.0.1", 25000)
    addrs = ", ".join(str(sock.getsockname()) for sock in server.sockets)
    print("Serving on", addrs)

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
