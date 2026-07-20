import { vi } from "vitest";

// ---------------------------------------------------------------------------
// Reusable typed signal (plan § Task 10 Step 3)
// ---------------------------------------------------------------------------

export class MockSignal<T> {
  private readonly listeners = new Set<(event: T) => void>();

  subscribe(listener: (event: T) => void): (event: T) => void {
    this.listeners.add(listener);
    return listener;
  }

  emit(event: T): void {
    for (const listener of this.listeners) listener(event);
  }

  clear(): void {
    this.listeners.clear();
  }
}

// ---------------------------------------------------------------------------
// system.run queue – callbacks are enqueued, not executed immediately
// ---------------------------------------------------------------------------

export const scheduledRuns: Array<() => void> = [];

export function flushScheduledRuns(): void {
  const snapshot = scheduledRuns.splice(0);
  for (const fn of snapshot) fn();
}

// ---------------------------------------------------------------------------
// Minecraft enum / type stubs required by bridge scripts at type-check time
// ---------------------------------------------------------------------------

export enum GameMode {
  survival = "survival",
  creative = "creative",
  adventure = "adventure",
  spectator = "spectator",
  // PascalCase aliases — production code uses these
  Creative = "creative",
}

export enum EntityComponentTypes {
  Health = "minecraft:health",
  Inventory = "minecraft:inventory",
}

export interface EntityHealthComponent {
  readonly componentId: string;
  currentValue: number;
}

export interface Vector3 {
  x: number;
  y: number;
  z: number;
}

export interface Dimension {
  id: string;
}

export interface Player {
  readonly id: string;
  readonly name: string;
  readonly location: Vector3;
  readonly dimension: Dimension;
  runCommand(command: string): Promise<void>;
  getComponent(componentId: string): EntityHealthComponent | EntityInventoryComponent | undefined;
  getTags(): string[];
  getGameMode(): GameMode;
}

export interface ItemStack {
  readonly typeId: string;
  amount: number;
  nameTag?: string;
}

export interface EntityInventoryComponent {
  readonly componentId: string;
  readonly container?: { readonly size: number; getItem(slot: number): ItemStack | undefined };
}

export interface ScriptEventCommandMessageAfterEvent {
  readonly id: string;
  readonly message: string;
  readonly sourceType: string;
  readonly sourceEntity?: Player;
}

// ---------------------------------------------------------------------------
// world / system – runtime-controllable mocks
// ---------------------------------------------------------------------------

export const world = {
  afterEvents: {
    worldLoad: new MockSignal<{}>(),
  },
  getAllPlayers: vi.fn<() => Player[]>().mockReturnValue([]),
  getPlayers: vi.fn<(options?: { name?: string }) => Player[]>().mockReturnValue([]),
  getDimension: vi.fn(),
};

export const system = {
  afterEvents: {
    scriptEventReceive: new MockSignal<ScriptEventCommandMessageAfterEvent>(),
  },
  run: vi.fn().mockImplementation((callback: () => void) => {
    scheduledRuns.push(callback);
  }),
  runInterval: vi.fn(),
};

// ---------------------------------------------------------------------------
// Reset helper (used by entry tests)
// ---------------------------------------------------------------------------

export function resetMinecraftMocks(): void {
  world.afterEvents.worldLoad.clear();
  system.afterEvents.scriptEventReceive.clear();
  scheduledRuns.splice(0);
  world.getAllPlayers.mockReset().mockReturnValue([]);
  world.getPlayers.mockReset().mockReturnValue([]);
  world.getDimension.mockReset();
  system.runInterval.mockReset();
}
