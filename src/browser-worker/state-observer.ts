import type { Locator } from "./types";
import type { InteractionBox, InteractionTargetState } from "./interaction-models";

function sameBox(a: InteractionBox | undefined, b: InteractionBox | undefined, tolerance = 1): boolean {
  if (!a || !b) return false;
  return Math.abs(a.x - b.x) <= tolerance
    && Math.abs(a.y - b.y) <= tolerance
    && Math.abs(a.width - b.width) <= tolerance
    && Math.abs(a.height - b.height) <= tolerance;
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export class InteractionStateObserver {
  constructor(private readonly stabilityWindowMs = 60) {}

  async observe(locator: Locator): Promise<InteractionTargetState> {
    const visible = await locator.isVisible().catch(() => false);
    if (!visible) return { visible: false, enabled: false, stable: false };

    const enabled = await locator.isEnabled().catch(() => false);
    const first = await locator.boundingBox().catch(() => null);
    if (!first) return { visible: true, enabled, stable: false };

    await sleep(this.stabilityWindowMs);
    const second = await locator.boundingBox().catch(() => null);
    const box = second ?? first;
    return {
      visible: true,
      enabled,
      stable: sameBox(first, second ?? undefined),
      box
    };
  }

  async waitUntilReady(locator: Locator, timeoutMs: number): Promise<InteractionTargetState> {
    const deadline = Date.now() + Math.max(1, timeoutMs);
    let last: InteractionTargetState = { visible: false, enabled: false, stable: false };

    while (Date.now() <= deadline) {
      last = await this.observe(locator);
      if (last.visible && last.enabled && last.stable && last.box) return last;
      await sleep(50);
    }

    return last;
  }
}
