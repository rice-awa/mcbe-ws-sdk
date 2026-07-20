# Basic MCBE WebSocket Server

A minimal runnable server built on `mcbe-ws-sdk`. It:

1. listens on `0.0.0.0:8080`;
2. accepts Minecraft Bedrock `/wsserver` connections;
3. subscribes to `PlayerMessage`;
4. prints player chat to the terminal; and
5. sends a `tellraw` reply through the SDK's `McbeOutboundDelivery`.

## Run

From the repository root, install the SDK in editable mode:

```bash
pip install -e ".[dev]"
```

Start the server:

```bash
python examples/basic-server/server.py
```

You can override the bind address and port:

```bash
python examples/basic-server/server.py --host 0.0.0.0 --port 8080
```

For frame-level send details and response-sender lifecycle events, raise the log
level:

```bash
python examples/basic-server/server.py --log-level DEBUG
```

The example configures compact console logging (no `[info     ]` padding).
Every line uses the same timestamped format:

```text
2026-07-20 16:40:16 [info] listening host=0.0.0.0 port=8081 url=ws://0.0.0.0:8081
```

Successful command responses are quiet; only non-zero `statusCode` values are
logged. Bedrock echoes tellraw output as `sender=外部` PlayerMessage events;
the example filters those echoes so they are not treated as player chat.

In the Minecraft Bedrock world, run:

```text
/wsserver <machine-running-the-server>:8080
```

Then send a chat message such as `hello`. The server replies in-game with the
sender's name and message.

## Notes

- Use the server machine's LAN IP when Minecraft runs on another machine; do
  not use `127.0.0.1` in that case.
- Allow TCP port `8080` through the host firewall.
- This example has no authentication. Use it only on a trusted local/LAN
  network; add authentication and authorization in `ConnectionHook` before
  using a public deployment.
- `player_event.sender` is used as the target because one `/wsserver` connection
  can carry messages from multiple players. Do not use the deprecated
  connection-level `ConnectionState.player_name` as the session identity.
