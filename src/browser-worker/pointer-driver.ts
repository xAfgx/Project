import type { Page } from "./types";
import type { InteractionPoint } from "./interaction-models";

export interface PointerClickOptions {
  button?: "left" | "right" | "middle";
  clickCount?: number;
}

export interface PointerDriver {
  moveTo(target: InteractionPoint): Promise<void>;
  click(target: InteractionPoint, options?: PointerClickOptions): Promise<void>;
  drag(path: readonly InteractionPoint[], durationMs?: number): Promise<void>;
}

const sleep = (ms: number): Promise<void> => new Promise(resolve => setTimeout(resolve, ms));

/**
 * Generic browser pointer driver for ordinary UI interactions.
 *
 * Coordinates are top-level viewport coordinates. Frame/OOPIF discovery and
 * coordinate resolution remain owned by the existing locator/session layer;
 * this driver never caches nodes, frame ids, execution contexts or CDP sessions.
 */
export class GhostCursorPointerDriver implements PointerDriver {
  private position: InteractionPoint = { x: 0, y: 0 };

  constructor(private readonly page: Page) {}

  async moveTo(target: InteractionPoint): Promise<void> {
    this.assertPoint(target);
    await this.page.mouse.move(target.x, target.y);
    this.position = { ...target };
  }

  async click(target: InteractionPoint, options: PointerClickOptions = {}): Promise<void> {
    this.assertPoint(target);
    await this.page.mouse.click(target.x, target.y, {
      button: options.button ?? "left",
      clickCount: options.clickCount ?? 1
    });
    this.position = { ...target };
  }

  async drag(path: readonly InteractionPoint[], durationMs = 300): Promise<void> {
    if (path.length < 2) throw new RangeError("PointerDriver.drag requires at least two points.");
    if (!Number.isFinite(durationMs) || durationMs < 0) {
      throw new RangeError("PointerDriver.drag durationMs must be a finite non-negative number.");
    }
    for (const point of path) this.assertPoint(point);

    const first = path[0]!;
    await this.page.mouse.move(first.x, first.y);
    await this.page.mouse.down({ button: "left" });

    const startedAt = Date.now();
    const segments = path.length - 1;
    try {
      for (let index = 1; index < path.length; index += 1) {
        const targetElapsed = durationMs * (index / segments);
        const remaining = targetElapsed - (Date.now() - startedAt);
        if (remaining > 0) await sleep(remaining);
        const point = path[index]!;
        await this.page.mouse.move(point.x, point.y);
      }
    } finally {
      await this.page.mouse.up({ button: "left" });
    }
    this.position = { ...path[path.length - 1]! };
  }

  private assertPoint(target: InteractionPoint): void {
    if (!Number.isFinite(target.x) || !Number.isFinite(target.y)) {
      throw new TypeError("Pointer coordinates must be finite numbers.");
    }
  }
}
