# shizuku

A minimal hand-rolled HTTP/1.1 server (`server.py`) with a hand-rolled
request parser (`parser.py`). Two routes are registered:

- `GET /ping`
- `POST /users`

All examples below were run against the live server on `127.0.0.1:25000`
(`python3 server.py`) and the output is copied verbatim from `curl -v`.

## GET /ping

Request:

```
curl -v http://127.0.0.1:25000/ping
```

Response:

```
> GET /ping HTTP/1.1
> Host: 127.0.0.1:25000
> User-Agent: curl/8.7.1
> Accept: */*
>
< HTTP/1.1 200 OK
< Content-Length: 6
<
online
```

`router.py` registers this route as `lambda r: Response(200, {}, b'online')`
— status 200, no extra headers, body `online`.

## POST /users

Request:

```
curl -v http://127.0.0.1:25000/users \
  -H "Content-Type: application/json" \
  -d '{"name":"Kek"}'
```

Response:

```
> POST /users HTTP/1.1
> Host: 127.0.0.1:25000
> User-Agent: curl/8.7.1
> Accept: */*
> Content-Type: application/json
> Content-Length: 14
>
< HTTP/1.1 200 OK
< Lol: Kek
< Content-Length: 0
<
```

`router.py` registers this route as
`lambda r: Response(200, {'Lol': 'Kek'}, b'')` — the request body is read and
discarded by the parser (so it must fully arrive before a response is sent),
but the handler ignores it and always replies with status 200, header
`Lol: Kek`, and an empty body.

## Error responses

### 404 — unregistered route

```
curl -v http://127.0.0.1:25000/nonexistent
```

```
< HTTP/1.1 404 Not Found
< Content-Length: 0
```

Any `(method, path)` pair not registered in `Router.routes` hits the
`KeyError` branch in `Router.dispatch` (router.py:14-16) and gets a bare 404.

### 400 — malformed request

`curl` can't easily produce a malformed HTTP request line, so this was
verified with a raw socket write instead:

```
printf 'BADREQUESTLINE\r\n\r\n' | nc 127.0.0.1 25000
```

```
HTTP/1.1 400 Bad Request
Content-Length: 0
```

`RequestParser.feed` raises `MalformedRequest` for things like a request
line that isn't exactly 3 space-separated fields, a bare LF, or whitespace
around a header name (parser.py). `server.py:handle` maps any parser error
to `Response(400, {}, b'')`, and `handle_request` closes the connection
right after sending it (server.py:34-35) — the 400 is always the last
response on that connection.

## Notes on request framing

- The parser is a state machine (`StartLineState` → `HeadersState` →
  `BodyState` → `FinalState`) driven off a single shared buffer, so a
  request is only turned into a `Request` once the full body (per
  `Content-Length`, default 0) has arrived — a slow/chunked client write
  will not get a response until then.
- Repeated headers are joined with a comma into one value
  (parser.py:82-84), matching how `curl -H` sends duplicate headers.
- There is no route for unhandled methods on a registered path (e.g.
  `DELETE /ping`) — that also falls into the generic 404, since routes are
  keyed on `(method, path)` together.
