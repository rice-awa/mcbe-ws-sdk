"""Bounded, best-effort packet diagnostics independent of legacy RAW events."""

from __future__ import annotations

import inspect
import json
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

import structlog

from mcbe_ws_sdk.tracing import TraceContext, current_trace

DiagnosticObserver = Callable[[dict[str, Any]], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class DiagnosticSettings:
    """Opt-in full packets and filters, applied before observers and logging.

    secrets are literal values, secret_keys are case-insensitive JSON keys.
    Filters match logical or wire request IDs. Full packets are size bounded.
    """

    full_packets: bool = False
    connection_ids: frozenset[str] = frozenset()
    request_ids: frozenset[str] = frozenset()
    secrets: tuple[str, ...] = ()
    secret_keys: frozenset[str] = frozenset({"password", "token", "secret", "authorization"})
    queue_maxsize: int = 256
    max_packet_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        if type(self.queue_maxsize) is not int or self.queue_maxsize <= 0:
            raise ValueError("queue_maxsize must be a positive integer")
        if type(self.max_packet_bytes) is not int or self.max_packet_bytes <= 0:
            raise ValueError("max_packet_bytes must be a positive integer")


def is_external_echo(sender: str, message_type: str | None = None) -> bool:
    """Classify Bedrock external-output provenance, never message contents.

    Explicit player/chat types override the localized external sender marker.
    This is a noise classification, not authentication or proof of delivery.
    """
    return sender in {"外部", "External"} and message_type not in ("player", "chat")


class Diagnostics:
    """Own one bounded observation lane for one connection.

    Slow async observers delay diagnostics only. Hosts must keep synchronous
    observers and logging handlers brief; they still run on the event loop.
    """

    def __init__(
        self,
        connection_id: UUID,
        settings: DiagnosticSettings | None = None,
        observers: tuple[DiagnosticObserver, ...] = (),
    ) -> None:
        from mcbe_ws_sdk.gateway.dispatch import BusinessDispatcher

        self.settings = settings or DiagnosticSettings()
        self._connection_id = str(connection_id)
        self._observers = observers
        self._secret_keys = {key.lower() for key in self.settings.secret_keys}
        self._lane = BusinessDispatcher(
            connection_id=connection_id,
            max_queue=self.settings.queue_maxsize,
            max_concurrent=1,
            name="diagnostics",
            log_rejections=False,
        )
        self.observer_failures = 0
        self.preparation_failures = 0
        self._wire_contexts: OrderedDict[str, TraceContext] = OrderedDict()

    @property
    def dropped_count(self) -> int:
        """Preparation failures, queue rejections and shutdown drops."""
        return self.preparation_failures + self._lane.rejected_count

    @property
    def task_count(self) -> int:
        return self._lane.task_count

    def remember(self, wire_request_id: str) -> None:
        """Keep bounded best-effort correlation for transport acknowledgements."""
        if wire_request_id:
            self._wire_contexts[wire_request_id] = current_trace()
            self._wire_contexts.move_to_end(wire_request_id)
            while len(self._wire_contexts) > self.settings.queue_maxsize:
                self._wire_contexts.popitem(last=False)

    def take_context(self, wire_request_id: str) -> TraceContext | None:
        """Consume recent wire correlation; request tables remain authoritative."""
        return self._wire_contexts.pop(wire_request_id, None)

    def _redact(self, value: Any, depth: int = 0) -> Any:
        # Bound later deepcopy/serialization too, independent of Python's stack limit.
        if depth > 64:
            raise ValueError("diagnostic nesting limit exceeded")
        if isinstance(value, dict):
            return {
                k: "[REDACTED]" if k.lower() in self._secret_keys else self._redact(v, depth + 1)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [self._redact(v, depth + 1) for v in value]
        if isinstance(value, str):
            for secret in self.settings.secrets:
                if secret:
                    value = value.replace(secret, "[REDACTED]")
        return value

    def _prepare(
        self, stage: str, *, packet: str | bytes | None = None, **fields: str | int | None
    ) -> dict[str, Any] | None:
        record: dict[str, Any] = {
            **asdict(current_trace()),
            **fields,
            "connection_id": self._connection_id,
            "stage": stage,
        }
        config = self.settings
        if config.connection_ids and self._connection_id not in config.connection_ids:
            return None
        if config.request_ids and not config.request_ids.intersection(
            {record.get("request_id"), record.get("wire_request_id")}
        ):
            return None
        if packet is not None:
            raw = packet.encode("utf-8") if isinstance(packet, str) else packet
            record["byte_size"] = len(raw)
            if config.full_packets:
                if len(raw) > config.max_packet_bytes:
                    record["packet_omitted"] = "size_limit"
                else:
                    try:
                        value = json.loads(raw)
                    except (ValueError, UnicodeDecodeError):
                        value = raw.decode("utf-8", errors="replace")
                    record["packet"] = value
        record = self._redact(record)
        return record

    def record(
        self, stage: str, *, packet: str | bytes | None = None, **fields: str | int | None
    ) -> None:
        """Enqueue best-effort diagnostics; discard unsafe preparation failures."""
        try:
            record = self._prepare(stage, packet=packet, **fields)
        except Exception:
            # Never log the exception or fall back to an unredacted packet.
            self.preparation_failures += 1
            return
        if record is None:
            return

        async def deliver() -> None:
            try:
                structlog.get_logger("mcbe_ws_sdk.diagnostics").debug("diagnostic", **record)
            except Exception:
                self.observer_failures += 1
            for observer in self._observers:
                try:
                    result = observer(deepcopy(record))
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    self.observer_failures += 1

        self._lane.submit(deliver, lane="diagnostics", label=stage)

    async def close(self) -> None:
        """Discard queued observations and cancel/join the active observer."""
        await self._lane.close()
        self._wire_contexts.clear()
