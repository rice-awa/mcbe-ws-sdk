# API Reference

Auto-generated from source via [mkdocstrings](https://mkdocstrings.github.io/).
Private names (`_*`) are filtered out. Prefer the public re-exports on
[`mcbe_ws_sdk`][mcbe_ws_sdk] when writing host code.

`McbeServer` is the only public server entry. `server.v2` exposes the
`V2Bridge` for capabilities, notifications and streams. See
[getting started](getting-started.md), [extension authors](extension-authors.md)
and [protocol](protocol-v2.md).

## Public package

::: mcbe_ws_sdk
    options:
      show_submodules: false

See [native events](native-events.md) for observer, interceptor and subscription contracts.

## Gateway

::: mcbe_ws_sdk.gateway
    options:
      show_submodules: true

## MCBEWS/2 runtime, transports and streams

::: mcbe_ws_sdk.profiles.mcbews_v2
    options:
      show_submodules: true

## Flow control

::: mcbe_ws_sdk.flow
    options:
      show_submodules: true

## Protocol models

::: mcbe_ws_sdk.protocol
    options:
      show_submodules: true

## Delivery

::: mcbe_ws_sdk.delivery
    options:
      show_submodules: true

## Command registry

::: mcbe_ws_sdk.command
    options:
      show_submodules: true

## Capability registry

::: mcbe_ws_sdk.capability
    options:
      show_submodules: true

## Config and logging

::: mcbe_ws_sdk.config

::: mcbe_ws_sdk.logging

## Tracing and diagnostics

See [diagnostic contracts](diagnostics.md) for lifecycle stages, packet privacy
and delivery results.

::: mcbe_ws_sdk.tracing

::: mcbe_ws_sdk.diagnostics

## Errors

::: mcbe_ws_sdk.errors
