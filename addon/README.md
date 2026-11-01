[![Languages](https://img.shields.io/badge/Languages-中文-blue?style=flat-square)](./README.zh.md)

# MCBE WS Bridge Addon

The TypeScript counterpart of `mcbe-ws-sdk`. It implements the Minecraft side
of MCBEWS/2: negotiation, the packet carrier, the capability registry and request
streams. The Python host implements the other side.

The current addon uses **V2 (MCBEWS/2)**, with two script event ids:

- `mcbews:negotiate_v2` — negotiation control;
- `mcbews:packet_v2` — logical message carrier.

Retired events (`mcbews:bridge_req`, `mcbews:session_req`, `mcbews:text_resp`)
are logged and rejected. Nothing is parsed, queued, answered or dispatched for
them. The addon cannot close a WebSocket owned by an old server; it only refuses
that server's business frames.

SDK, npm project, pack, protocol and Script API versions are independent.
See [version numbers](../docs/migration.md#version-numbers) for the current values.

## Requirements

- Minecraft Bedrock with Script API and the target world's
  **Experiments → Beta APIs** enabled.
- Node.js 22 for building the pack.
- Dependencies pinned in `package.json`:
  - `@minecraft/server` 2.8.0
  - `@minecraft/server-ui` 2.1.0
  - `@minecraft/server-gametest` `1.0.0-beta.1.26.30-stable`
- Both pack manifests target `min_engine_version` `[1, 26, 30]`.

Automated builds and tests do not prove live-game support. Verify the game
version and platform you deploy to.

## Build

```bash
npm ci
npm run build       # tsc type-check + esbuild bundle
npm run mcaddon     # assemble the .mcaddon
```

The default package ships the complete capability set: the two read-only
snapshots, `demo.echo`, the `demo.ticks` stream and `run_world_command`. Import
`dist/packages/mc-ws-sdk.mcaddon`.

The lean package contains only the two read-only snapshots:

```bash
npm run build:base
npm run mcaddon:base
```

`--base` or `MCBE_BASE=1` selects `scripts/main.ts`; the default build uses
`scripts/main.demo.ts`. Both variants write the same final artifact,
`dist/scripts/main.js`, and the lean pack is `mc-ws-sdk-base.mcaddon`. `lib/` is
only intermediate type-check output and is not loaded by the game.

The bundle externalizes `@minecraft/server`, `@minecraft/server-ui` and
`@minecraft/server-gametest`; the pack manifest supplies the module versions.

## Enable in a world

1. Import the generated `.mcaddon` into Minecraft, or copy the behavior and
   resource packs.
2. Open the **target world** settings (create a world, or **Edit** an existing
   one).
3. Under **Experiments**, enable **Beta APIs**.
4. Activate the behavior pack (and the companion resource pack if prompted).
5. Enter the world, then connect with `/wsserver <host-ip>:8080`.

Without Beta APIs the scripts never load: negotiation times out and no bridge
activity appears in-game. Enabling experiments may make the world ineligible
for some achievements; use a dedicated test world when possible.

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

The router activates only after the world is ready. A hello that arrives before
activation is retained as one sanitized attempt; other pre-ready business is
dropped, never replayed. Each connected peer has its own epoch, fragments and
pending requests. A new peer never replaces another.

Downlink uses `scriptevent mcbews:packet_v2`; uplink uses chat frames sent by
the simulated player `MCBEWS_BRIDGE`. That name is transport attribution, not
authentication.

## Register a capability

```typescript
import { registerCapability } from "./bridge/router";

const registration = registerCapability("custom.echo", (_method, payload, context) => ({
  text: payload.text,
  metadata: context.metadata,
}));
// unregisterCapability(registration)
```

Handlers receive `(method, payload, context)` and return an object or a Promise.
Context includes `caller`, `requestVersion`, `metadata`, `client` and
`cancellation`. An optional validator returns a message to reject the payload.

Duplicate names throw unless `replace: true` is passed. Unregistering during an
in-flight call does not change that call; later calls no longer see the name.

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

Producers yield JSON items and do not need to know the total length. Large items
are fragmented by the SDK and delivered once. Duplicate stream names throw.
Consumers stop with cancellation; producers must check `context.cancellation`.

The router also exposes `getV2Endpoint(epoch)` for advanced host code that needs
the per-connection endpoint directly.

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

Other capabilities such as `find_entities` are not bundled. Custom entity
queries are host-defined and must validate their own scope.

## World command safety

For player-relative commands, call `run_world_command` with
`{"command": "testfor @s", "player": "Alex"}`. The exact online player name
supplies the executor, position and dimension; an unavailable player returns an
error. Omitting `player` keeps the overworld dimension context. The example's
`!cmd` supplies the chat sender automatically.

`successCount` is the number of successful command applications. A zero count
is reported explicitly; a completed bridge call alone does not establish a
world change.

`run_world_command` ships in the default package, but its policy still applies:

- the default policy is denylist-only: any entrypoint on the command
  denylist (`execute`, `script`, `op`, `setblock`, `fill`, and others) is
  rejected;
- pass an explicit allowlist to `handleRunWorldCommand` to narrow it further;
- every call must still be authorized by the Python host.

The bridge is **not** a security boundary. Transport attribution, the simulated
player and metadata identity do not replace authentication or authorization.
The addon only provides the defensive command filter above.

## Troubleshooting

| Symptom                                       | Check                                                                                                |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Negotiation times out                         | The pack is not active, the target world is wrong, or **Beta APIs** is off                           |
| `UNSUPPORTED_PROTOCOL` in logs                | A retired script event reached the addon; update the Python host or the addon so both speak MCBEWS/2 |
| `UNSUPPORTED_METHOD` for `!echo` or `!stream` | The lean `--base` package is installed. Use the default demo build                                   |
| `!cmd` is rejected                            | The command is on the command denylist, or the pack is the lean `--base` build                       |
| No bridge activity in-game                    | Scripts did not load. Confirm Beta APIs, content log and pack activation                             |
| `demo.ticks` stops early                      | The consumer cancelled or exceeded a stream limit                                                    |

## License

MIT
