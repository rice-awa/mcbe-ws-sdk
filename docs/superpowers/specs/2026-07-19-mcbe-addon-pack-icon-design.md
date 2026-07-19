# MCBE Addon Pack Icon Design

## Goal

Generate a new candidate pack icon for the MCBE AI Agent addon. The candidate
must visually fit a Minecraft Bedrock Edition addon while signalling that the
pack provides an SDK. This task produces an image asset only; it does not alter
either pack's active `pack_icon.png`.

## Visual Design

The image is a square, pixel-art Minecraft-style pack icon with a lightweight,
high-contrast composition:

- A single light-gray stone development block fills the central portion of the
  image.
- The block front carries a bright lava-orange `</>` code mark.
- A small gold, pixel-font `SDK` label sits at the lower-right of the block.
- A flat charcoal-gray background leaves generous negative space around the
  block.
- No teal, cyan, green, redstone circuitry, particles, characters, UI panels,
  scenery, gradients, or decorative text.

## Output and Boundaries

- Generate one 1024 by 1024 PNG candidate with GPT Image 2 using the
  environment file at `/home/riceawa/garden-gpt-image-2/.env`.
- Save the rendered prompt and generated image using the image skill's naming
  conventions in the workspace.
- Inspect the generated result for a clear Minecraft-like silhouette, correct
  warm palette, and legible code/SDK marks.
- Do not replace either existing pack icon:
  `addon/behavior_packs/mcbe-ws-sdk/pack_icon.png` and
  `addon/resource_packs/mcbe-ws-sdk/pack_icon.png`.

## Error Handling

If the configured image gateway cannot generate the image, retain the prompt
file and report the gateway error without changing pack assets.

## Verification

Confirm the final candidate is a valid square PNG and visually inspect it at
full size and at a reduced pack-list scale.
