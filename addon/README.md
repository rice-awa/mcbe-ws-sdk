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
npm run build       # runs tsc, output in lib/
```

## License

MIT
