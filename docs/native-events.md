# Native events and business interception

`McbeServer` uses managed business lanes for every connection, so native
subscriptions are always available. `PlayerMessage` continues to use its typed
hook. Other native events use their wire names without adding `WsEventType`
members.

## Subscribe per connection

Use these APIs inside `on_connected` or another managed host callback:

```python
from mcbe_ws_sdk import InterceptResult, McbeServer, NativeEvent, NoOpHook


class HostHook(NoOpHook):
    async def on_connected(self, state):
        self.token = await state.native_events.subscribe("PlayerMessage", self.observe)
        await state.native_events.intercept("PlayerMessage", self.filter_message)

    def observe(self, event: NativeEvent):
        print(event.name, event.packet)

    def filter_message(self, event: NativeEvent):
        return InterceptResult("allow")


server = McbeServer(hook=HostHook())
```

For another game event, pass its exact wire name and optionally implement
`NativeEventHook.on_native_event(state, event)`. `NativeEvent.packet` preserves
the complete header, body and extra fields. Each callback receives a private
copy. `PlayerMessage` retains its typed `on_player_message` hook; `parsed`
reflects the final accepted message. The five lifecycle hooks are
`on_connected`, `on_disconnected`, `on_player_message`, `on_command_response`
and `on_error`.

## Lifecycle and ordering

- Every registration, including duplicate callbacks, has a unique token.
  `await state.native_events.unsubscribe(token)` removes that registration only.
- The first registration for a name sends subscribe; removing its last observer
  or interceptor sends unsubscribe. These frames use the control-priority
  sender. Completion confirms transport send, not game support or
  acknowledgement.
- `PlayerMessage` is reserved for SDK bridge traffic: it is subscribed during
  connection setup and never unsubscribed by listener removal.
- Registration is bounded by `dispatch.event_queue_maxsize`; event names are at
  most 256 UTF-8 bytes and contain no whitespace or control characters.
  Subscription operations time out using `dispatch.command_timeout_seconds`;
  an uncertain send closes the local registry. Unregistering an unknown or
  closed token returns `False`.
- Callbacks run in registration order. Local removal skips callbacks that have
  not started; it does not cancel a running callback. Connection close clears
  registrations, discards queued work and cancels/joins managed tasks. No
  unsubscribe is sent on the closing transport.

## Observers and interceptors

Observers are notification-only: return values are ignored and exceptions are
logged before continuing to the next observer. They see the original event even
if business delivery is rejected. Observation runs on the bounded observation
lane and never awaits user code in the WebSocket reader. Slow async observers
may delay later observations; overflow rejects notifications rather than
blocking replies. Synchronous callbacks must stay brief and nonblocking.

Interceptors run inside the bounded business dispatcher. Return
`InterceptResult("allow")`, `InterceptResult("reject")`, or
`InterceptResult("transform", replacement_event)`. Each transform feeds the next
interceptor; exceptions or invalid results reject business delivery and skip the
remaining interceptors. Rejection leaves observers active. Transformations must
preserve the event name, event-purpose envelope and player sender; they cannot
manufacture reserved bridge/control traffic.

Same-player messages keep FIFO order through the entire chain and hook; other
players can progress concurrently. Other native events share one FIFO business
lane. Business queue overflow rejects that delivery. Callback exceptions are
isolated by the managed dispatcher.

Command replies/errors and reserved addon frames are correlated or classified
before native extensions and never enter interceptor chains. Reserved prefixes
from an untrusted sender remain consumed by the bridge gate. Native interception
cannot authenticate a caller or replace host authorization.

## Verification boundary

`PlayerMessage` is the SDK's established and tested native event. Other names
are opaque passthrough: unit tests verify registration and raw packet delivery,
not that a particular event exists in a Bedrock version. Verify names and
payloads against your game version before depending on them. Accepting an
event-name string does not claim game-version support.
