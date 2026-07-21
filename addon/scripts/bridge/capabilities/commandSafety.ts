const COMMAND_DENYLIST = [
  "stop",
  "reload",
  "kick",
  "op",
  "deop",
  "execute",
  "script",
  "gamemode",
  "setblock",
  "fill",
  "clone",
  "function",
  "permission",
  "whitelist",
  "allowlist",
  "ban",
  "ban-ip",
  "pardon",
  "pardon-ip",
  "save",
  "save-all",
  "save-off",
  "save-on",
  "setmaxplayers",
  "alwaysday",
  "daylock",
  "difficulty",
  "gamerule",
  "tickingarea",
  "structure",
  "camera",
  "inputpermission",
  "transfer",
  "connect",
  "changesetting",
];

export function extractCommandEntrypoints(command: string): string[] {
  const entrypoints: string[] = [];
  const segments = command
    .toLowerCase()
    .split(/[;\n\r]+/)
    .map((segment) => segment.trim())
    .filter(Boolean);

  for (const segment of segments) {
    const tokens = segment.split(/\s+/).filter(Boolean);
    if (tokens.length === 0) {
      continue;
    }

    entrypoints.push(tokens[0]);

    for (let index = 0; index < tokens.length - 1; index += 1) {
      if (tokens[index] === "run") {
        entrypoints.push(tokens[index + 1]);
      }
    }
  }

  return entrypoints;
}

export function findDeniedCommand(command: string): string | undefined {
  const entrypoints = extractCommandEntrypoints(command);
  return entrypoints.find((entrypoint) => COMMAND_DENYLIST.includes(entrypoint));
}

/**
 * Check whether a command is allowed under the given policy.
 *
 * - When `allowlist` is a non-null array: every entrypoint must be in the
 *   allowlist (case-insensitive). Denylist still wins if both would apply.
 * - When `allowlist` is `null`: behave like denylist-only (`findDeniedCommand`).
 *
 * Returns the first disallowed entrypoint name, or `undefined` if allowed.
 */
export function findDisallowedCommand(command: string, allowlist: string[] | null): string | undefined {
  const denied = findDeniedCommand(command);
  if (denied !== undefined) {
    return denied;
  }

  if (allowlist === null) {
    return undefined;
  }

  const allowed = new Set(allowlist.map((entry) => entry.toLowerCase()));
  const entrypoints = extractCommandEntrypoints(command);
  return entrypoints.find((entrypoint) => !allowed.has(entrypoint));
}
