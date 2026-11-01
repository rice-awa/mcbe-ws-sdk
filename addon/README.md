[![Languages](https://img.shields.io/badge/Languages-中文-blue?style=flat-square)](./README.zh.md)

# MCBE WS Bridge Addon

The TypeScript counterpart of `mcbe-ws-sdk`. It does the Minecraft side of
MCBEWS/2: negotiation, the packet carrier, the capability registry and request
streams. The Python host does the other side.

The current addon uses **V2 (MCBEWS/2)** and listens for two script event ids:

- `mcbews:negotiate_v2` carries the negotiation control messages.
- `mcbews:packet_v2` carries the logical messages.

The retired events (`mcbews:bridge_req`, `mcbews:session_req` and
`mcbews:text_resp`) are logged and rejected. The addon does not parse, queue,
answer or dispatch them. It also cannot close a WebSocket that belongs to an old
server, so it simply refuses that server's business frames.

The SDK, the npm project, the pack, the protocol and the Script API are each
versioned independently. See [version numbers](../docs/migration.md#version-numbers)
for the current values.

## Requirements

- Minecraft Bedrock with Script API support, and **Experiments → Beta APIs**
  enabled in the target world.
- Node.js 22 to build the pack.
- Dependencies pinned in `package.json`:
  - `@minecraft/server` 2.8.0
  - `@minecraft/server-ui` 2.1.0
  - `@minecraft/server-gametest` `1.0.0-beta.1.26.30-stable`
- Both pack manifests set `min_engine_version` to `[1, 26, 30]`.

Passing automated builds and tests does not prove the addon works in a live
game. Check the game version and platform you deploy to.

## Build

```bash
npm ci
npm run build       # tsc type-check + esbuild bundle
npm run mcaddon     # assemble the .mcaddon
```

The default package ships the complete capability set: the two read-only
snapshots, `demo.echo`, the `demo.ticks` stream and `run_world_command`. To use
it, import `dist/packages/mc-ws-sdk.mcaddon`.

The lean package contains only the two read-only snapshots:

```bash
npm run build:base
npm run mcaddon:base
```

Passing `--base` or setting `MCBE_BASE=1` makes the build start from
`scripts/main.ts`, while the default build starts from `scripts/main.demo.ts`.
Both variants write the same final file, `dist/scripts/main.js`, and the lean
pack is named `mc-ws-sdk-base.mcaddon`. `lib/` only holds intermediate
type-check output, and the game never loads it.

The bundle leaves `@minecraft/server`, `@minecraft/server-ui` and
`@minecraft/server-gametest` as external modules, and the pack manifest supplies
their versions.

## Enable in a world

1. Import the generated `.mcaddon` into Minecraft, or copy the behavior and
   resource packs.
2. Open the **target world** settings (create a world, or **Edit** an existing
   one).
3. Under **Experiments**, enable **Beta APIs**.
4. Activate the behavior pack (and the companion resource pack if prompted).
5. Enter the world, then connect with `/wsserver <host-ip>:8080`.

Without Beta APIs the scripts never load, so negotiation times out and you see
no bridge activity in the game. Enabling experiments may also make the world
ineligible for some achievements, so use a dedicated test world when you can.

## How it works

```text
scriptEventReceive
├── mcbews:negotiate_v2  → challenge / confirmation / lease / close
├── mcbews:packet_v2     → reassemble FRAME → V2Endpoint
└── retired ids          → log rejection only

V2Endpoint
├── CapabilityRegistry   → registerCapability / dispatch by name
└── stream handlers      → registerV2Stream
```

The router activates only once the world is ready. A hello that arrives before
that is kept as a single sanitized attempt, and any other business message that
arrives early is dropped and never replayed. Each connected peer has its own
epoch, fragments and pending requests, and a new peer never replaces an existing
one.

Downlink messages go out through `scriptevent mcbews:packet_v2`. Uplink messages
arrive as chat frames sent by the simulated player `MCBEWS_BRIDGE`. That name
only tells you which transport carried the message; it is not authentication.

## Register a capability

```typescript
import { registerCapability } from "./bridge/router";

const registration = registerCapability("custom.echo", (_method, payload, context) => ({
  text: payload.text,
  metadata: context.metadata,
}));
// unregisterCapability(registration)
```

A handler receives `(method, payload, context)` and returns an object or a
Promise. The context includes `caller`, `requestVersion`, `metadata`, `client`
and `cancellation`. An optional validator rejects the payload by returning a
message.

Registering a name that already exists throws unless you pass `replace: true`.
Unregistering while a call is in flight does not affect that call, but later
calls no longer find the name.

## Register a stream

```typescript
import { registerV2Stream } from "./bridge/router";

const unregister = registerV2Stream("custom.ticks", async function* (_method, params, context) {
  for (let index = 0; index < (params.count ?? 5); index += 1) {
    if (context.cancellation?.cancelled) return;
    yield { index };
  }
});
```

A producer yields JSON items one at a time and does not need to know the total
length in advance. The SDK fragments large items and delivers each one once,
after reassembly. Registering a duplicate stream name throws. A consumer stops a
stream by cancelling it, so producers must check `context.cancellation`.

The router also exposes `getV2Endpoint(epoch)` for advanced host code that needs
direct access to the per-connection endpoint.

## Built-in capabilities

Every package registers:

| Capability               | Description                                                     |
| ------------------------ | --------------------------------------------------------------- |
| `get_player_snapshot`    | Player name, health, tags, coordinates, dimension and game mode |
| `get_inventory_snapshot` | Inventory slots, item IDs, counts and custom names              |

The default demo build additionally registers:

| Capability / stream | Description                                                     |
| ------------------- | --------------------------------------------------------------- |
| `demo.echo`         | Echoes the payload with request metadata                        |
| `demo.ticks`        | Finite or unbounded index stream; cancellation stops production |
| `run_world_command` | World-command handler; the command denylist applies             |

Other capabilities, such as `find_entities`, are not bundled. Custom entity
queries are defined by the host, which must also validate their scope.

## World command safety

To run a command relative to a player, call `run_world_command` with
`{"command": "testfor @s", "player": "Alex"}`. The exact name of an online player
supplies the executor, position and dimension, and the call returns an error if
that player is not available. If you omit `player`, the command keeps the
overworld dimension context. The example's `!cmd` passes the chat sender
automatically.

`successCount` is the number of times the command was applied successfully, and
a zero is reported explicitly. A completed bridge call alone does not mean the
world changed.

`run_world_command` ships in the default package, but its policy still applies:

- The default policy is denylist-only: any command whose entrypoint is on the
  denylist (`execute`, `script`, `op`, `setblock`, `fill` and others) is
  rejected.
- To narrow it further, pass an explicit allowlist to `handleRunWorldCommand`.
- Every call must still be authorized by the Python host.

The bridge is **not** a security boundary. Transport attribution, the simulated
player and metadata identity cannot replace authentication or authorization.
The addon provides only the defensive command filter described above.

## Troubleshooting

| Symptom                                       | Check                                                                                                 |
| --------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Negotiation times out                         | The pack is not active, you joined the wrong world, or **Beta APIs** is off                           |
| `UNSUPPORTED_PROTOCOL` in logs                | The addon received a retired script event; update the Python host or the addon so both speak MCBEWS/2 |
| `UNSUPPORTED_METHOD` for `!echo` or `!stream` | The lean `--base` package is installed. Switch to the default demo build                              |
| `!cmd` is rejected                            | The command is on the command denylist, or the pack is the lean `--base` build                        |
| No bridge activity in-game                    | The scripts did not load. Check Beta APIs, the content log and whether the pack is active             |
| `demo.ticks` stops early                      | The consumer cancelled the stream, or the stream hit a limit                                          |

## License

MIT
