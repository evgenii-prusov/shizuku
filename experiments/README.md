# experiments — what `await` replaces

Two servers with identical observable behaviour, written to be read side by side:

- **`blocking_echo.py`** — the obvious version. One connection at a time.
- **`selectors_echo.py`** — the same thing on a hand-rolled event loop, many
  connections at once, in one thread, with no `asyncio`.

Throwaway teaching code. Nothing in `shizuku` imports it; it stays committed as
the "before" picture for the real server.

## Where the state went

The blocking version keeps its state **on the C stack**. Inside `echo_handler`
the client socket is a local variable, and *where I am in the conversation* —
have I read yet, am I about to send — is implicit in which line is currently
executing. The stack frame remembers for me. When the connection ends the frame
is discarded and control returns to `echo_server`, whose own frame still holds
the listening socket, so `accept()` can hand me the next client.

Going non-blocking destroys that. `select()` tells me "fd 7 is readable" and
nothing else — it has no memory of what fd 7 was doing. So every local that
mattered has to be hoisted into an explicit object: `ConnectionState` holds the
client socket, the peer address, the outbound buffer and the callback, one
instance per client, kept alive by the selector registration. (The listener has
no per-connection state, so it isn't one of these — it is just a tagged
`partial` over `accept_handler`.)

That transformation — locals becoming fields of a heap object — is the whole
exercise.

## Where the process blocks

`sel.select()` is the only call in the program that sleeps, and it is the only
one allowed to.

Non-blocking mode does **not** mean a socket is "not ready yet". It means every
call does whatever it can do at this instant and returns immediately: `recv()`
hands back whatever bytes are already in the kernel buffer, and raises
`BlockingIOError` if there are none. The socket is fully usable throughout;
there is simply no data right now.

`select()` blocks when no registered descriptor is ready and returns the moment
one becomes ready. While blocked, the process is parked in a kernel wait queue,
off the run queue, consuming no CPU. Being *concurrent* and *asleep when idle*
at the same time is the property the rewrite buys — either one alone is easy.

## Why the interest set has to be maintained

When the outbound buffer drains I call `sel.modify()` to drop `EVENT_WRITE`,
and that is not tidiness. A socket counts as writable whenever there is free
space in its kernel send buffer — for an idle connection, that is *always
true*. Leave `EVENT_WRITE` registered with nothing to send and `select()`
returns instantly on every call, forever: the program stops being an event loop
and becomes a spin loop burning a full core while nothing happens.

`EVENT_READ` is not like this — a socket with no incoming data is genuinely not
readable, so it is safe to leave registered. Writability is the asymmetric
case, because it is the default state rather than the exceptional one.

Hence the rule: **the interest set is per-connection state that changes as the
connection changes, not configuration set once at registration.**

## What `sendall()` was hiding

`send()` hands the kernel as much as it currently has room for and returns the
count, which may be less than I gave it. `sendall()` loops over `send()` until
everything is accepted — and blocks to do it, which is exactly what the event
loop may never do.

So the loop has to run that retry by hand, spread across events instead of
across iterations: append to `connection.buffer`, ask for `EVENT_WRITE`, send
what fits, drop the sent prefix, and only clear the write interest once the
buffer is empty. The 500 KB test below exists to prove that path works.

## What `await` still adds

This loop hands out readiness; it does not hand back the stack. Every handler
must return to the loop immediately, so multi-step protocols have to be
unrolled by hand into state stored on the connection — fine for echo, unbearable
for HTTP.

A coroutine frame is a heap-allocated stack frame that can be suspended and
resumed, so straight-line code comes back while the loop keeps the state.
**`await` gives back what `select()` took away.**

## Running it

```
python3 experiments/selectors_echo.py      # listens on :25000
nc localhost 25000                          # in two or three terminals
```

Verified behaviour:

| Check | Result |
|---|---|
| Two clients typing at once | both echo, interleaved, single thread |
| Peer sends RST before the first read | logged; other connections unaffected |
| 500 KB payload | round-trips byte-identical (partial-write path) |
| Idle with clients connected | ~0% CPU — the loop is asleep in `select()` |

Run `blocking_echo.py` and connect twice to see the contrast: the second client
completes its TCP handshake (the kernel parks it in the listen backlog) but gets
no echo at all until the first client disconnects. Connected, and starving.

## Known limits

Deliberate, since this is a demo and not a server:

- EOF with a non-empty outbound buffer drops those bytes. A real server flushes
  first, then closes.
- No read backpressure: a client that sends without reading grows
  `connection.buffer` without bound.
- `buffer = buffer[sent:]` copies the remainder on every write. `memoryview`
  is the fix, and it matters in the parser, not here.
