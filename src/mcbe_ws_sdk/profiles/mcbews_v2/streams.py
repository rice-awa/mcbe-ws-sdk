"""Bounded request streams over the existing v2 logical-message transport."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from mcbe_ws_sdk.capability import CapabilityContext
from mcbe_ws_sdk.profiles.mcbews_v2.codec import MANIFEST, dumps
from mcbe_ws_sdk.tracing import trace_scope

StreamHandler = Callable[[str, Mapping[str, Any], CapabilityContext], AsyncIterator[Any]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


class StreamError(RuntimeError):
    """A classified stream failure; already executed side effects are not undone.

    ``code`` is the protocol error code and ``message`` the remote or local
    reason, so hosts can render them without parsing the exception text.
    """

    def __init__(self, code: str, message: str = "stream failed") -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class StreamRegistration:
    """Identity handle for removing a registered producer."""

    name: str


class StreamRegistry:
    """Register async-iterator producers independently of atomic capabilities."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[StreamRegistration, StreamHandler]] = {}

    def register(self, name: str, handler: StreamHandler) -> StreamRegistration:
        """Register a producer; duplicate names are rejected."""
        if name in self._entries:
            raise ValueError(f"stream already registered: {name}")
        registration = StreamRegistration(name)
        self._entries[name] = (registration, handler)
        return registration

    def unregister(self, registration: StreamRegistration) -> bool:
        """Remove only the producer identified by this registration."""
        entry = self._entries.get(registration.name)
        if entry is None or entry[0] is not registration:
            return False
        del self._entries[registration.name]
        return True

    def get(self, name: str) -> StreamHandler | None:
        entry = self._entries.get(name)
        return entry[1] if entry else None


class IncomingStream(AsyncIterator[Any]):
    """Consume reassembled items; use ``async with`` to cancel on early exit."""

    def __init__(self, owner: StreamManager, stream_id: str) -> None:
        self._owner = owner
        self.id = stream_id
        self._items: deque[tuple[Any, int]] = deque()
        self._bytes = 0
        self._seq = 0
        self._ended = False
        self._error: StreamError | None = None
        self._ready = asyncio.Event()
        self._timer: asyncio.TimerHandle | None = None
        self._refresh()

    def _refresh(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = asyncio.get_running_loop().call_later(
            self._owner.idle_seconds, self._owner.fail, self, "TIMEOUT"
        )

    def _finish(self, error: StreamError | None = None) -> None:
        if self._ended:
            return
        self._ended = True
        self._error = error
        if self._timer is not None:
            self._timer.cancel()
        if error is not None:
            self._owner.buffered_bytes -= self._bytes
            self._items.clear()
            self._bytes = 0
        self._owner.incoming.pop(self.id, None)
        if self._items:
            self._owner.draining.add(self)
        self._ready.set()

    def __aiter__(self) -> IncomingStream:
        return self

    async def __anext__(self) -> Any:
        while not self._items:
            if self._error is not None:
                raise self._error
            if self._ended:
                raise StopAsyncIteration
            self._ready.clear()
            try:
                await self._ready.wait()
            except asyncio.CancelledError:
                await self.aclose()
                raise
        value, size = self._items.popleft()
        self._bytes -= size
        self._owner.buffered_bytes -= size
        if not self._items:
            self._owner.draining.discard(self)
        return value

    async def aclose(self) -> None:
        """Cancel this stream and release buffered items without affecting peers."""
        if not self._ended:
            self._owner.fail(self, "CANCELLED")
        else:
            self._owner.buffered_bytes -= self._bytes
            self._bytes = 0
            self._items.clear()
            self._owner.draining.discard(self)

    async def __aenter__(self) -> IncomingStream:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()


class StreamManager:
    """Own stream lifetimes and bounded consumer buffers for one endpoint."""

    def __init__(
        self,
        send: Send,
        registry: StreamRegistry,
        *,
        overrides: dict[str, int] | None = None,
    ) -> None:
        self.cfg: dict[str, int] = dict(MANIFEST["streams"])
        for key, value in (overrides or {}).items():
            if key not in self.cfg or type(value) is not int or not 0 < value <= self.cfg[key]:
                raise ValueError(f"invalid stream limit: {key}")
            self.cfg[key] = value
        self.idle_seconds = self.cfg["idle_ms"] / 1000
        self.send = send
        self.registry = registry
        self.incoming: dict[str, IncomingStream] = {}
        self.draining: set[IncomingStream] = set()
        self.producers: dict[str, asyncio.Task[None]] = {}
        self.opening: dict[str, asyncio.Task[None]] = {}
        self.controls: set[asyncio.Task[None]] = set()
        self.tombstones: dict[str, float] = {}
        self.buffered_bytes = 0
        self.closed = False

    def _admit(self) -> None:
        if self.closed:
            raise StreamError("CANCELLED", "endpoint closed")
        if len(self.incoming) + len(self.producers) + len(self.draining) >= self.cfg["max_active"]:
            raise StreamError("LIMIT_EXCEEDED", "stream capacity exhausted")

    async def open(self, message: dict[str, Any]) -> IncomingStream:
        """Install the receiver before sending the opening message."""
        self._admit()
        stream = IncomingStream(self, message["id"])
        self.incoming[stream.id] = stream

        async def send_open() -> None:
            await self.send(message)

        send_task = asyncio.create_task(send_open(), name=f"v2-stream-open:{stream.id}")
        self.opening[stream.id] = send_task
        try:
            await send_task
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            if caller is not None and caller.cancelling():
                self.fail(stream, "CANCELLED")
                raise
            raise stream._error or StreamError("CANCELLED", "opening send cancelled") from None
        except Exception:
            self.fail(stream, "CANCELLED")
            raise
        finally:
            self.opening.pop(stream.id, None)
        if stream._error is not None:
            raise stream._error
        return stream

    def _control(self, message: dict[str, Any]) -> None:
        if self.closed or len(self.controls) >= self.cfg["max_active"]:
            return

        async def send() -> None:
            async with asyncio.timeout(self.idle_seconds):
                await self.send(message)

        task = asyncio.create_task(send(), name=f"v2-stream-control:{message['id']}")
        self.controls.add(task)

        def done(completed: asyncio.Task[None]) -> None:
            self.controls.discard(completed)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(done)

    def fail(self, stream: IncomingStream, code: str) -> None:
        """Terminate a consumer once and ask its producer to stop."""
        if stream._ended:
            return
        stream._finish(StreamError(code))
        opening = self.opening.get(stream.id)
        if opening is not None:
            opening.cancel()
        self._control({"v": "MCBEWS/2", "kind": "stream_cancel", "id": stream.id})

    def receive(self, message: dict[str, Any], context: CapabilityContext) -> None:
        """Route a complete stream message without waiting on business work."""
        if self.closed:
            return
        kind, stream_id = message["kind"], message["id"]
        if kind == "stream_cancel":
            task = self.producers.get(stream_id)
            if task is not None:
                task.cancel()
            return
        if kind == "stream_open":
            now = time.monotonic()
            self.tombstones = {
                key: expiry for key, expiry in self.tombstones.items() if expiry > now
            }
            if stream_id in self.producers or stream_id in self.tombstones:
                return
            try:
                self._admit()
                if len(self.producers) + len(self.tombstones) >= self.cfg["tombstones"]:
                    raise StreamError("LIMIT_EXCEEDED")
            except StreamError:
                if len(self.producers) + len(self.tombstones) < self.cfg["tombstones"]:
                    self.tombstones[stream_id] = now + self.cfg["tombstone_ms"] / 1000
                self._control(
                    {
                        "v": "MCBEWS/2",
                        "kind": "stream_error",
                        "id": stream_id,
                        "seq": 0,
                        "error": {"code": "LIMIT_EXCEEDED", "message": "stream capacity exhausted"},
                    }
                )
                return
            handler = self.registry.get(message["method"])
            with trace_scope(
                connection_id=context.connection_id,
                request_id=stream_id,
                trace_id=context.metadata.get("sdk.trace_id"),
            ):
                task = asyncio.create_task(
                    self._produce(message, context, handler), name=f"v2-stream:{stream_id}"
                )
            self.producers[stream_id] = task

            def done(completed: asyncio.Task[None]) -> None:
                self.producers.pop(stream_id, None)
                if not self.closed:
                    self.tombstones[stream_id] = time.monotonic() + self.cfg["tombstone_ms"] / 1000
                if not completed.cancelled():
                    completed.exception()

            task.add_done_callback(done)
            return
        stream = self.incoming.get(stream_id)
        if stream is None:
            return
        if message["seq"] != stream._seq:
            self.fail(stream, "INVALID_MESSAGE")
            return
        if kind == "stream_end":
            stream._finish()
        elif kind == "stream_error":
            stream._finish(StreamError(message["error"]["code"], message["error"]["message"]))
        else:
            size = len(dumps(message["item"]).encode())
            if (
                len(stream._items) >= self.cfg["buffer_items"]
                or stream._bytes + size > self.cfg["buffer_bytes"]
                or self.buffered_bytes + size > self.cfg["connection_bytes"]
            ):
                self.fail(stream, "LIMIT_EXCEEDED")
                return
            stream._items.append((message["item"], size))
            stream._bytes += size
            self.buffered_bytes += size
            stream._seq += 1
            stream._refresh()
            stream._ready.set()

    async def _produce(
        self, message: dict[str, Any], context: CapabilityContext, handler: StreamHandler | None
    ) -> None:
        seq = 0
        iterator: AsyncIterator[Any] | None = None
        base = {"v": "MCBEWS/2", "id": message["id"], "meta": message.get("meta", {})}
        try:
            if handler is None:
                raise StreamError("UNSUPPORTED_METHOD")
            if not isinstance(message["params"], dict):
                raise StreamError("INVALID_PARAMS")
            iterator = aiter(handler(message["method"], message["params"], context))
            while True:
                try:
                    async with asyncio.timeout(self.idle_seconds):
                        item = await anext(iterator)
                except StopAsyncIteration:
                    break
                async with asyncio.timeout(self.idle_seconds):
                    await self.send({**base, "kind": "stream_item", "seq": seq, "item": item})
                seq += 1
            async with asyncio.timeout(self.idle_seconds):
                await self.send({**base, "kind": "stream_end", "seq": seq})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, StreamError)
                else "TIMEOUT"
                if isinstance(exc, TimeoutError)
                else "HANDLER_FAILED"
            )
            self._control(
                {
                    **base,
                    "kind": "stream_error",
                    "seq": seq,
                    "error": {"code": code, "message": "stream producer failed"},
                }
            )
        finally:
            if iterator is not None:
                close = getattr(iterator, "aclose", None)
                if close is not None:
                    async with asyncio.timeout(self.idle_seconds):
                        await close()

    def close(self) -> None:
        """Stop producers and controls and wake every consumer on disconnect."""
        if self.closed:
            return
        self.closed = True
        for stream in [*self.incoming.values(), *self.draining]:
            if stream._ended:
                self.buffered_bytes -= stream._bytes
                stream._bytes = 0
                stream._items.clear()
            else:
                stream._finish(StreamError("CANCELLED", "endpoint closed"))
        self.draining.clear()
        for task in [*self.opening.values(), *self.producers.values(), *self.controls]:
            task.cancel()
        self.tombstones.clear()

    async def wait_closed(self) -> None:
        """Wait for cancelled producers to execute their cleanup."""
        await asyncio.gather(
            *self.opening.values(), *self.producers.values(), *self.controls, return_exceptions=True
        )
