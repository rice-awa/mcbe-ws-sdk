"""High-level server entry point with reliable dispatch and negotiated addon calls."""

from __future__ import annotations

from dataclasses import replace

from mcbe_ws_sdk.addon import AddonBridgeService
from mcbe_ws_sdk.command import CommandRegistry
from mcbe_ws_sdk.config import GatewaySettings
from mcbe_ws_sdk.diagnostics import DiagnosticObserver, DiagnosticSettings
from mcbe_ws_sdk.gateway.handler import MessageSurfaceConfig
from mcbe_ws_sdk.gateway.hook import ConnectionHook, LegacyUiChatHook
from mcbe_ws_sdk.gateway.server_facade import McbeServerFacade
from mcbe_ws_sdk.gateway.sink import ResponseSink
from mcbe_ws_sdk.profiles.mcbews_v2.runtime import V2Bridge


class McbeServer(McbeServerFacade):
    """Serve connections whose hooks can await commands and addon responses.

    This entry point always selects reliable dispatch, preserving all other
    settings. It owns a negotiating v2 bridge unless one is injected. Legacy
    peers retain supported v1 capabilities; metadata and streams require v2.
    Use ``McbeServerFacade`` to retain serial callbacks and its original defaults.
    """

    def __init__(
        self,
        *,
        settings: GatewaySettings | None = None,
        hook: ConnectionHook | None = None,
        legacy_ui_chat_hook: LegacyUiChatHook | None = None,
        sink: ResponseSink | None = None,
        addon: AddonBridgeService | None = None,
        registry: CommandRegistry | None = None,
        surface: MessageSurfaceConfig | None = None,
        diagnostics: DiagnosticSettings | None = None,
        diagnostic_observers: tuple[DiagnosticObserver, ...] = (),
        v2: V2Bridge | None = None,
    ) -> None:
        selected = settings if settings is not None else GatewaySettings()
        selected = replace(selected, dispatch=replace(selected.dispatch, mode="reliable"))
        self._bridge = v2 if v2 is not None else V2Bridge()
        super().__init__(
            settings=selected,
            hook=hook,
            legacy_ui_chat_hook=legacy_ui_chat_hook,
            sink=sink,
            addon=addon,
            registry=registry,
            surface=surface,
            diagnostics=diagnostics,
            diagnostic_observers=diagnostic_observers,
            v2=self._bridge,
        )

    @property
    def v2(self) -> V2Bridge:
        """Negotiated addon requests, streams and host capability registrations."""
        return self._bridge
