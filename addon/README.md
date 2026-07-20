[![Languages](https://img.shields.io/badge/Languages-中文-blue?style=flat-square)](./README.zh.md)

# MCBE WS Bridge Addon

Minimal Minecraft Bedrock WebSocket bridge addon — the TypeScript counterpart of the
`mcbe-ws-sdk` Python package. This addon handles the protocol layer:

- `constants.ts` — wire protocol constants matching Python `AddonProtocolConfig`
- `chunking.ts` — encode/decode for `MCBEAI|RESP` and `MCBEAI|UI_CHAT` fragments
- `router.ts` — subscribes to `mcbeai:bridge_request` scriptevents, dispatches to the
  built-in capability registry, and ships the response back
- `responseSync.ts` — reassembles `mcbeai:ai_resp` chunks and invokes a callback
- `toolPlayer.ts` — marks a player as a towered transport per response delivery (tellraw)
- `bootstrap.ts` — safe init lifecycle (early init → after-world-load)

The addon owns the full capability-handling lifecycle. Capabilities are dispatched
through the built-in default capability registry in `capabilities/index.ts`.
The Python SDK has no inbound capability registry — it sends requests and receives
responses.

## Usage

From a Python host, `AddonBridgeService` + `AddonBridgeClient` sends capability
requests; the addon picks them up and delivers them in-game.

From the addon side, capabilities are registered by appending entries to
the default registry in `capabilities/index.ts`. The router dispatches
automatically.

### TypeScript host (override mode)

When a TypeScript host needs full control, it can override the built-in registry:

```ts
import { setCapabilityHandler } from "./bridge/router";

setCapabilityHandler((capability, payload) => {
  // dispatch by name; return { ok: true, ... } or { ok: false, error: "..." }
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

## Built-in Capabilities

The addon ships a ready-to-use capability registry
(`scripts/bridge/capabilities/index.ts`) as a runnable reference example.

| Capability | File | Description |
| --- | --- | --- |
| `get_player_snapshot` | `getPlayerSnapshot.ts` | Get a player snapshot (name, health, tags, coordinates, dimension, game mode) |
| `get_inventory_snapshot` | `getInventorySnapshot.ts` | Get a player inventory snapshot (slots, item ID, count, custom name) |
| `run_world_command` | `runWorldCommand.ts` | Run an MC command in the world (guarded by the `commandSafety.ts` blacklist) |

Developers add custom capabilities by appending entries to
the default registry in `capabilities/index.ts`, coexisting with the
built-in capabilities and still looked up by `router.ts` by capability name.

> Note: `find_entities` is not yet bundled. The current protocol's `scriptevent` does not carry the source-player context, while an entity query needs that context to scope the search; once the protocol supports player-sourced scriptevents, this capability will be added.

## License

MIT
