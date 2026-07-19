[![Languages](https://img.shields.io/badge/Languages-中文-blue?style=flat-square)](./README.zh.md)

# MCBE WS Bridge Addon

Minimal Minecraft Bedrock WebSocket bridge addon — the TypeScript counterpart of the
`mcbe-ws-sdk` Python package. This addon handles the protocol layer:

- `constants.ts` — wire protocol constants matching Python `AddonProtocolConfig`
- `chunking.ts` — encode/decode for `MCBEAI|RESP` and `MCBEAI|UI_CHAT` fragments
- `router.ts` — subscribes to `mcbeai:bridge_request` scriptevents, calls a host-registered handler, ships the response back
- `responseSync.ts` — reassembles `mcbeai:ai_resp` chunks and invokes a callback
- `toolPlayer.ts` — marks a player as a towered transport per response delivery (tellraw)
- `bootstrap.ts` — safe init lifecycle (early init → after-world-load)

The host registers its own capability handler (`setCapabilityHandler`),
response sender (`setResponseSender`), and AI response handler
(`setAiRespHandler`). The addon provides only the transport; decisions about
what to do with the payload are the host's.

## Usage

From a Python host, `McbeServerFacade` + `AddonBridgeService` sends capability
requests and AI responses; the addon picks them up and delivers them in-game.

From a TypeScript host:

```ts
import { setCapabilityHandler, setResponseSender } from "./bridge/router";
import { setAiRespHandler } from "./bridge/responseSync";
import { sendBridgeResponseChunks } from "./bridge/toolPlayer";

setResponseSender((requestId, body) => sendBridgeResponseChunks(requestId, body));

setCapabilityHandler((capability, payload) => {
  // dispatch by name; return { ok: true, ... } or { ok: false, error: "..." }
});

setAiRespHandler((playerName, role, text) => {
  // display the reassembled AI response
});
```

Then in `main.ts`:

```ts
import { initializeEarly, initializeAfterWorldLoad } from "./bootstrap";

initializeEarly();
initializeAfterWorldLoad(() => { /* register handlers here */ });
```

## Build

```bash
npm install
npm run build       # tsc type-check gate (intermediate output to lib/) + esbuild bundle
npm run mcaddon     # assemble .mcaddon on top of build output
```

The build pipeline has two steps:

1. **`tsc` type-check gate**: type-checks the source, emitting intermediate output to `lib/` (used for type checking only — not the final artifact).
2. **esbuild `bundle` task**: produces the real final artifact `dist/scripts/main.js`, then `copyArtifacts` copies `dist/scripts` into the behavior pack, matching the manifest `"entry": "scripts/main.js"`.

The artifact that loads in-game is `scripts/main.js` — treat that as authoritative, not the intermediate output under `lib/`.

## Built-in Base Capabilities

The addon ships a ready-to-use base capability registry
(`scripts/bridge/capabilities/index.ts`) as a runnable reference example.
When the host has not injected a custom handler via `setCapabilityHandler`,
`router.ts` falls back to this registry; once the host registers a handler,
it fully overrides the defaults.

| Capability | File | Description |
| --- | --- | --- |
| `get_player_snapshot` | `getPlayerSnapshot.ts` | Get a player snapshot (name, health, tags, coordinates, dimension, game mode) |
| `get_inventory_snapshot` | `getInventorySnapshot.ts` | Get a player inventory snapshot (slots, item ID, count, custom name) |
| `run_world_command` | `runWorldCommand.ts` | Run an MC command in the world (guarded by the `commandSafety.ts` blacklist) |

Developers have two ways to add a custom capability:

1. Implement a `CapabilityHandler` (`(capability, payload) => { ok, payload }`) and register it via `setCapabilityHandler` — this overrides the default registry and hands dispatch entirely to the host.
2. Append a new entry to `defaultCapabilityRegistry` in `capabilities/index.ts`, coexisting with the built-in capabilities, still looked up and invoked by `router.ts` by capability name.

> Note: `find_entities` is not yet bundled. The current protocol's `scriptevent` does not carry the source-player context, while an entity query needs that context to scope the search; once the protocol supports player-sourced scriptevents, this capability will be added.

## License

MIT
