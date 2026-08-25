from request import Request
from response import Response


class Router:
    def __init__(self):
        self.routes = {}

    def register(self, method: str, path: str, handler):
        self.routes[(method, path)] = handler

    def dispatch(self, request: Request) -> Response:
        try:
            handler = self.routes[(request.method, request.path)]
        except KeyError:
            return Response(404, {}, b'')
        try:
            res = handler(request)
            return res
        except Exception:
            return Response(500, {}, b'')
