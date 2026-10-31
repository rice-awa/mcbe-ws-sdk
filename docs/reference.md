# API Reference

Auto-generated from source via [mkdocstrings](https://mkdocstrings.github.io/).
Private names (`_*`) are filtered out. Prefer the public re-exports on
[`mcbe_ws_sdk`][mcbe_ws_sdk] when writing host code.

Start new checkout hosts with `McbeServer`; retain `McbeServerFacade` for its
legacy defaults. These new exports are unreleased and are not in PyPI `0.2.1`.
See [migration](migration.md) and [extension authors](extension-authors.md).

## Public package

::: mcbe_ws_sdk
    options:
      show_submodules: false

See [native events](native-events.md) for observer, interceptor and subscription contracts.

## Gateway

::: mcbe_ws_sdk.gateway
    options:
      show_submodules: true

## Addon bridge

::: mcbe_ws_sdk.addon
    options:
      show_submodules: true

## Negotiated v2 and streams

::: mcbe_ws_sdk.profiles.mcbews_v2.runtime

::: mcbe_ws_sdk.profiles.mcbews_v2.streams

## Flow control

::: mcbe_ws_sdk.flow
    options:
      show_submodules: true

## Protocol models

::: mcbe_ws_sdk.protocol
    options:
      show_submodules: true

## Profiles

::: mcbe_ws_sdk.profiles
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

## Config & logging

::: mcbe_ws_sdk.config

::: mcbe_ws_sdk.logging

## Tracing and diagnostics

See [diagnostic contracts](diagnostics.md) for packet privacy and delivery stages.

::: mcbe_ws_sdk.tracing

::: mcbe_ws_sdk.diagnostics

## Errors

::: mcbe_ws_sdk.errors
