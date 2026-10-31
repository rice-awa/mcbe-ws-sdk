# Native events and business interception

This page describes the unreleased checkout, not PyPI `0.2.1`. See the [migration guide](migration.md) for installation and compatibility.

Native extensions require reliable dispatch, enabled by default in `McbeServer`.
Legacy `McbeServerFacade` callback ordering and defaults remain unchanged. Existing PlayerMessage hooks continue
working. Generic events use their wire names without adding WsEventType members.

## Subscribe per connection

Use these APIs inside on_connected or another managed host callback:

~~~python
from mcbe_ws_sdk import McbeServer
from mcbe_ws_sdk.gateway.hook import NoOpHook
from mcbe_ws_sdk.gateway.native_events import InterceptResult, NativeEvent

class HostHook(NoOpHook):
    async def on_connected(self, state):
        self.token = await state.native_events.subscribe("PlayerMessage", self.observe)
        await state.native_events.intercept("PlayerMessage", self.filter_message)

    def observe(self, event: NativeEvent):
        print(event.name, event.packet)

    def filter_message(self, event: NativeEvent):
        return InterceptResult("allow")

server = McbeServer(hook=HostHook())
~~~

For another game event, pass its exact wire name and optionally implement
NativeEventHook.on_native_event(state, event). NativeEvent.packet preserves the
complete header, body and extra fields. Each callback receives a private copy.
PlayerMessage retains its typed on_player_message hook; parsed commands reflect
the final accepted message. Existing typed hooks need no adapter. The explicit
LegacyUiChatHookAdapter remains available for the old UI callback signature.

## Lifecycle and ordering

- Every registration, including duplicate callbacks, has a unique token. Await
  state.native_events.unsubscribe(token) to remove that registration only.
- The first registration for a name sends subscribe; removing its last observer
  or interceptor sends unsubscribe. These frames use the control-priority sender.
  Completion confirms transport send, not game support or acknowledgement.
- PlayerMessage is reserved for SDK bridge traffic: it is subscribed during
  connection setup and never unsubscribed by listener removal.
- Registration is bounded by dispatch.event_queue_maxsize and event names by
  256 UTF-8 bytes without whitespace/control characters. Subscription operations
  time out using dispatch.command_timeout_seconds; uncertain sends close the local
  registry. Unregistering an unknown/closed token returns False.
- Callbacks run in registration order. Local removal skips callbacks that have
  not started; it does not cancel a currently running callback. Connection close
  clears registrations, discards queued work and cancels/joins managed tasks.
  Close sends no unsubscribe on the closing transport.

## Observers and interceptors

Observers are notification-only: return values are ignored and exceptions are
logged before continuing to the next observer. They see the original event even
if business delivery is rejected. Observation runs on the bounded observation
lane and never awaits user code in the WebSocket reader. Slow async observers may
delay later observations; overflow rejects notifications rather than blocking
replies. Synchronous callbacks must stay brief and nonblocking.

Interceptors run inside the bounded business dispatcher. Return
InterceptResult("allow"), InterceptResult("reject"), or
InterceptResult("transform", replacement_event). Each transform feeds the next
interceptor; exceptions or invalid results reject business delivery and skip the
remaining interceptors. Rejection leaves observers active. Transformations must
preserve the event name, event-purpose envelope and player sender; they cannot
manufacture reserved bridge/control traffic. Same-player messages keep FIFO order
through the entire chain and hook; other players can progress concurrently.
Other native events share one FIFO business lane. Business queue overflow rejects
that delivery. Callback exceptions are isolated by the managed dispatcher.

Command replies/errors and reserved addon frames are correlated/classified before
native extensions and never enter interceptor chains. Reserved prefixes from an
untrusted sender remain consumed by the bridge gate. Native interception cannot
authenticate a caller or replace host authorization.

## Verification boundary

PlayerMessage is the SDK's established and tested native event. Other names are
opaque passthrough: unit tests verify registration and raw packet delivery, not
that any particular event exists in a Bedrock version. Verify names and payloads
against your game version before depending on them. No new game-version support
is claimed by accepting an event-name string.
