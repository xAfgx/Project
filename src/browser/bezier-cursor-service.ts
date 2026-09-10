import type { Locator, Page } from "../browser-worker/types";
import {
  GhostCursorUiInteractionHelper,
  UiPoint,
  type UiClickOptions,
  type UiFillOptions,
  type UiFocusOptions,
  type UiHoverOptions,
  type UiScrollOptions,
  type UiSelectOptions
} from "../browser-worker/ui-interaction-helper";

/** @deprecated Use GhostCursorUiInteractionHelper directly in new worker code. */
export class BezierCursorService {
  private readonly helpers = new WeakMap<Page, GhostCursorUiInteractionHelper>();

  async moveTo(page: Page, target: UiPoint): Promise<void> {
    await this.forPage(page).moveToPoint(target);
  }

  async clickLocator(page: Page, locator: Locator, options?: UiClickOptions): Promise<void> {
    await this.forPage(page).click(locator, options);
  }

  async fillLocator(page: Page, locator: Locator, value: string, options?: UiFillOptions): Promise<void> {
    await this.forPage(page).fill(locator, value, options);
  }

  async selectLocator(page: Page, locator: Locator, value: string, options?: UiSelectOptions): Promise<void> {
    await this.forPage(page).select(locator, value, options);
  }

  async focusLocator(page: Page, locator: Locator, options?: UiFocusOptions): Promise<void> {
    await this.forPage(page).focus(locator, options);
  }

  async hoverLocator(page: Page, locator: Locator, options?: UiHoverOptions): Promise<void> {
    await this.forPage(page).hover(locator, options);
  }

  async scrollLocatorIntoView(page: Page, locator: Locator, options?: UiScrollOptions): Promise<void> {
    await this.forPage(page).scrollIntoView(locator, options);
  }

  private forPage(page: Page): GhostCursorUiInteractionHelper {
    const existing = this.helpers.get(page);
    if (existing) return existing;
    const helper = new GhostCursorUiInteractionHelper(page);
    this.helpers.set(page, helper);
    return helper;
  }
}
