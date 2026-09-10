import type { Locator, Page } from "./types";
import { InteractionEngine } from "./interaction-engine";
import type { InteractionOutcomeExpectation } from "./interaction-policies";

export interface UiPoint {
  x: number;
  y: number;
}

export interface UiMoveOptions {
  stepDelayMs?: number;
  seed?: number | string;
}

export interface UiClickOptions extends UiMoveOptions {
  button?: "left" | "right" | "middle";
  clickCount?: number;
  attempts?: number;
  expected?: InteractionOutcomeExpectation;
}

export interface UiFillOptions {
  attempts?: number;
  seed?: number | string;
  expected?: InteractionOutcomeExpectation;
}

export interface UiSelectOptions extends UiFillOptions {}
export interface UiFocusOptions extends UiFillOptions {}
export interface UiHoverOptions extends UiFillOptions {}
export interface UiScrollOptions extends UiFillOptions {}

export interface UiInteractionHelper {
  moveTo(target: Locator, options?: UiMoveOptions): Promise<void>;
  moveToPoint(point: UiPoint, options?: UiMoveOptions): Promise<void>;
  click(target: Locator, options?: UiClickOptions): Promise<void>;
  fill(target: Locator, value: string, options?: UiFillOptions): Promise<void>;
  select(target: Locator, value: string, options?: UiSelectOptions): Promise<void>;
  focus(target: Locator, options?: UiFocusOptions): Promise<void>;
  hover(target: Locator, options?: UiHoverOptions): Promise<void>;
  scrollIntoView(target: Locator, options?: UiScrollOptions): Promise<void>;
}

/**
 * Backwards-compatible facade for normal UI automation.
 * InteractionEngine owns readiness/outcome/retries and the seeded Bezier cursor
 * path. Challenge handling stays separate.
 */
export class GhostCursorUiInteractionHelper implements UiInteractionHelper {
  private readonly engine: InteractionEngine;
  private readonly seedNamespace: string;

  constructor(private readonly page: Page) {
    // Do not inject the direct pointer driver here: doing so bypasses
    // InteractionEngine.movePointer(), which is where the deterministic seeded
    // Bezier path is generated. Keeping the engine on its native Page.mouse
    // path makes every supplied seed affect both target variation and movement.
    this.engine = new InteractionEngine(page);
    const runtimeSeed = String(page["interactionSeed"] ?? "").trim();
    this.seedNamespace = runtimeSeed || "ares-interaction";
  }

  async moveTo(target: Locator, options: UiMoveOptions = {}): Promise<void> {
    await target.scrollIntoViewIfNeeded();
    await target.waitFor({ state: "visible" });
    const box = await target.boundingBox();
    if (!box) throw new Error("UI target has no visible bounding box.");
    await this.moveToPoint({ x: box.x + box.width / 2, y: box.y + box.height / 2 }, options);
  }

  async moveToPoint(target: UiPoint, options: UiMoveOptions = {}): Promise<void> {
    await this.engine.moveToPoint(target, options.seed ?? `${this.seedNamespace}:move`);
  }

  async click(target: Locator, options: UiClickOptions = {}): Promise<void> {
    const result = await this.engine.click(target, {
      seed: options.seed ?? `${this.seedNamespace}:click`,
      attempts: options.attempts,
      expected: options.expected,
      button: options.button,
      clickCount: options.clickCount
    });
    this.assertSuccess("click", result.success, result.failureReason);
  }

  async fill(target: Locator, value: string, options: UiFillOptions = {}): Promise<void> {
    const result = await this.engine.fill(target, value, {
      attempts: options.attempts,
      seed: options.seed ?? `${this.seedNamespace}:fill`,
      expected: options.expected
    });
    this.assertSuccess("fill", result.success, result.failureReason);
  }

  async select(target: Locator, value: string, options: UiSelectOptions = {}): Promise<void> {
    const result = await this.engine.select(target, value, {
      attempts: options.attempts,
      seed: options.seed ?? `${this.seedNamespace}:select`,
      expected: options.expected
    });
    this.assertSuccess("select", result.success, result.failureReason);
  }

  async focus(target: Locator, options: UiFocusOptions = {}): Promise<void> {
    const result = await this.engine.focus(target, {
      attempts: options.attempts,
      seed: options.seed ?? `${this.seedNamespace}:focus`,
      expected: options.expected
    });
    this.assertSuccess("focus", result.success, result.failureReason);
  }

  async hover(target: Locator, options: UiHoverOptions = {}): Promise<void> {
    const result = await this.engine.hover(target, {
      attempts: options.attempts,
      seed: options.seed ?? `${this.seedNamespace}:hover`,
      expected: options.expected
    });
    this.assertSuccess("hover", result.success, result.failureReason);
  }

  async scrollIntoView(target: Locator, options: UiScrollOptions = {}): Promise<void> {
    const result = await this.engine.scrollIntoView(target, {
      attempts: options.attempts,
      seed: options.seed ?? `${this.seedNamespace}:scroll`,
      expected: options.expected
    });
    this.assertSuccess("scroll", result.success, result.failureReason);
  }

  private assertSuccess(action: string, success: boolean, failureReason?: string): void {
    if (!success) throw new Error(`Interaction ${action} failed: ${failureReason ?? "unknown"}`);
  }
}
