"""Demonstrates registering a CapabilityHandler, subclassing NoOpHook,
and running a McbeServerFacade lifecycle — all without a real Minecraft client.

This smoke-test starts the facade's WebSocket listener, idles for 3 seconds,
then triggers a graceful shutdown so you can observe the startup and shutdown
log lines. No real MCBE client is needed — the ``on_connected`` hook fires
only when a client actually connects, so this example primarily exercises the
start/stop lifecycle.
"""

import asyncio
import logging
from typing import Any

from mcbe_ws_sdk import (
    CapabilityContext,
    CapabilityRegistry,
    McbeServerFacade,
    NoOpHook,
)
from mcbe_ws_sdk.config import GatewaySettings, WebsocketTransportConfig

logger = logging.getLogger("greeting")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)


class GreetingHandler:
    """Handles the ``"greet"`` capability.

    Registered only for ``"greet"`` in the :class:`CapabilityRegistry`; any
    other capability name falls through to the registry's built-in
    :class:`~mcbe_ws_sdk.capability.registry.LoggingStubHandler`.
    """

    async def handle(self, ctx: CapabilityContext) -> dict[str, Any]:
        name = ctx.payload.get("name", "world")
        message = f"Hello, {name}!"
        logger.info("greeting_handler_invoked", capability=ctx.capability, name=name)
        return {"ok": True, "greeting": message}


class GreetingHook(NoOpHook):
    """Minimal connection-lifecycle hook: logs connect / disconnect events."""

    async def on_connected(self, state) -> None:
        logger.info("client_connected", connection_id=str(state.id))

    async def on_disconnected(self, state) -> None:
        logger.info("client_disconnected", connection_id=str(state.id))


async def main() -> None:
    """Build the facade, start it, idle, then shut down gracefully."""

    # 1. Build the capability registry and register the greeting handler.
    registry = CapabilityRegistry()
    registry.register("greet", GreetingHandler())

    # 2. Build the facade with a non-conflicting port and the capability registry.
    #    The default sink (SilentResponseSink) and remaining collaborators
    #    work fine for a smoke test.
    hook = GreetingHook()
    settings = GatewaySettings(
        websocket=WebsocketTransportConfig(host="127.0.0.1", port=9876)
    )
    facade = McbeServerFacade(settings=settings, hook=hook, capabilities=registry)

    logger.info("Starting capability-greeting facade...")

    # 3. Run the facade's WebSocket listener in a background task.
    server_task = asyncio.create_task(facade.run_lifetime())

    # Let the server listen for a few seconds.
    await asyncio.sleep(3)

    # 4. Trigger graceful shutdown.
    await facade.stop()
    await server_task  # wait for run_lifetime to unwind fully

    logger.info("Facade stopped.")


if __name__ == "__main__":
    asyncio.run(main())
