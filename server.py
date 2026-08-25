import asyncio
from asyncio import StreamReader, StreamWriter, start_server

from parser import RequestParser
from request import Request
from response import Response
from router import Router

router = Router()


def handle(parser: RequestParser, router: Router, data: bytes) -> Response | None:
    request: Request = parser.feed(data)
    if request is None:
        if parser.error:
            return Response(400, {}, b'')
        return None
    
    response = router.dispatch(request)
    return response


async def handle_request(reader: StreamReader, writer: StreamWriter) -> Response:
    parser: RequestParser = RequestParser()
    response: Response | None
    while True:
        data = await reader.read()
        if not data:
            break
        response = handle(parser, router, data)
        if response:
            writer.write(response.serialize)
            await writer.drain()
    writer.close()


async def main():
    server = await start_server(handle_request, "127.0.0.1", 25000)
    addrs = ", ".join(str(sock.getsocknamei()) for sock in server.sockets)
    print("Serving on", addrs)

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
