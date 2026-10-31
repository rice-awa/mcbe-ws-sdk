# Addon Capability Call Example

This example retains the compatible v1 public API. It needs no game or addon
and does not demonstrate negotiated v2 or streams. For the unreleased managed
entry, see [addon-server](../addon-server/README.md).

Complete in-memory round trip through the `mcbe-ws-sdk` public API.

```bash
python examples/addon-capability-call/capability_call.py
```

Expected output: `{"ok":true,"greeting":"Hello, Steve"}`
