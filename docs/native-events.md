# Native events and business interception

Every connection in `McbeServer` runs its business callbacks on queues managed
by the SDK, so native event subscriptions are always available. `PlayerMessage`
still goes through its typed hook. Any other native event is referred to by its
wire name, so you do not need to add members to `WsEventType`.

## Subscribe per connection

Call these APIs from `on_connected` or from another callback that the SDK runs
for your host:

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

To listen for another game event, pass its exact wire name. You can also
implement `NativeEventHook.on_native_event(state, event)` as the place where that
event enters your business logic. `NativeEvent.packet` keeps the complete header,
body and any extra fields, and every callback receives its own copy.
`PlayerMessage` keeps its typed `on_player_message` hook, and `parsed` is
computed from the message that was finally let through. The five lifecycle hooks
are `on_connected`, `on_disconnected`, `on_player_message`,
`on_command_response` and `on_error`.

## Lifecycle and ordering

- Each registration gets its own token, even when you register the same callback
  twice. `await state.native_events.unsubscribe(token)` removes only the
  registration that the token belongs to.
- The first registration for an event name sends a subscribe request, and
  removing its last observer or interceptor sends an unsubscribe request. Both
  are sent with control priority. When the call returns, it only confirms that
  the frame was sent; it does not mean the game supports the event or has
  acknowledged the subscription.
- `PlayerMessage` is reserved for SDK bridge traffic. It is subscribed during
  connection setup, and removing your listeners never unsubscribes it.
- The number of registrations per connection is limited by
  `dispatch.event_queue_maxsize`. An event name is at most 256 UTF-8 bytes and
  cannot contain whitespace or control characters. Subscription operations time
  out after `dispatch.command_timeout_seconds`. If a send ends in an uncertain
  state, the local registry is closed. Unregistering an unknown or already
  closed token returns `False`.
- Callbacks run in the order they were registered. Removing a registration
  skips callbacks that have not started yet, but does not cancel one that is
  already running. When the connection closes, the SDK clears the registrations,
  discards queued work, and cancels and waits for its managed tasks. It does not
  send an unsubscribe over a transport that is closing.

## Observers and interceptors

Observers only receive notifications. Their return values are ignored, and an
exception is logged before the next observer runs. Observers see the original
event even if business delivery is rejected. They run on a separate observation
queue with a size limit, and the WebSocket reader never awaits your code. A slow
async observer can delay later observations, and when that queue overflows the
SDK rejects new notifications instead of blocking replies. Synchronous
callbacks must be short and must not block.

Interceptors run inside the business dispatcher, which also has a size limit.
Return `InterceptResult("allow")`, `InterceptResult("reject")`, or
`InterceptResult("transform", replacement_event)`. Each transformed event is
passed to the next interceptor. An exception or an invalid result rejects
business delivery and skips the remaining interceptors, while observers keep
working. A transformation must keep the event name, the event's purpose and the
player sender, and it cannot create reserved bridge or control traffic.

Messages from the same player stay in first-in, first-out order through the
whole interceptor chain and the hook, while other players can progress
concurrently. All other native events share one first-in, first-out business
queue. If the business queue overflows, that delivery is rejected. The SDK
contains exceptions raised in callbacks, so one failing callback does not stop
the rest.

Before native extensions see anything, the SDK matches command replies and
errors to their requests and classifies reserved addon frames, so these never
enter an interceptor chain. A reserved prefix sent by an untrusted sender is
still consumed by the bridge gate. Native interception cannot authenticate a
caller or replace authorization on your host.

## Verification boundary

`PlayerMessage` is the native event that the SDK has long supported and tested.
Any other name is passed through as-is. The unit tests check registration and
delivery of the raw packet; they do not check that the event exists in a given
Bedrock version. Verify the event name and payload against your game version
before relying on them. The SDK accepting an event-name string does not mean
that version of the game supports it.
