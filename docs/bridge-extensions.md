# Bridge extensions

Fragment storage and optional MCBEWS/1 business adapters live in the same package.
`ReassemblyCore` handles ordering, duplicate detection, UTF-8 size limits and expiry.
Each connection shares its buffer-count and total-byte budgets across bridge and UI
channels. Identical duplicates do not refresh expiry; adapters release completed
buffers and map expired capability fragments to pending-request failures.

`mcbe_ws_sdk.extensions.mcbews_v1` owns UI chat, session and approval DTOs,
codecs, control classification and text/session delivery. Existing root and
`profiles.mcbews_v1` imports remain compatible exports. Wire bytes, final-frame
usage and atomic `SESSION_RESPONSE_TOO_LARGE` replies remain unchanged.

To use the capability bridge without its optional business handlers:

```python
from mcbe_ws_sdk import AddonBridgeService, AddonBridgeSettings, McbeServerFacade

addon = AddonBridgeService(AddonBridgeSettings(), extensions=False)
facade = McbeServerFacade(addon=addon)
```

Connection lifecycle, native commands and custom capabilities still work.
Reserved UI/session/approval frames are consumed without decoding or callbacks.
The default is `extensions=True`, preserving existing hosts. A disabled session
raises `ProtocolError` on an explicit `handle_ui_chat_chunk` call.

On the addon, `bridge/reassembly.ts` stores opaque fragments; the optional
`extensions/textResponse.ts` adapter validates role, conversation and usage fields.
`bridge/responseSync.ts` remains its compatibility export. Native command and
capability routing does not require registering the text response handler.

Boundary checks live in `tests/unit/test_reassembly_extensions.py` and
`addon/tests/reassembly.test.ts`; canonical v1 vectors remain authoritative.
