import type { Locator, Page } from "../browser-worker/types";
import { observeCheckoutOutcome } from "../browser-worker/checkout-outcome-observer";
import { GhostCursorUiInteractionHelper } from "../browser-worker/ui-interaction-helper";

const FINAL_SUBMIT_PATTERNS = [
  /\bpay now\b/i,
  /\bplace order\b/i,
  /\bsubmit order\b/i,
  /\bcomplete order\b/i,
  /jetzt bezahlen/i,
  /zahlungspflichtig bestellen/i,
  /kostenpflichtig bestellen/i,
  /bestellung (?:aufgeben|abschicken)/i,
  /verbindlich bestellen/i,
  /bestellung bestätigen/i,
  /kauf abschließen/i,
  /\bjetzt kaufen\b/i
];

const SAFE_ADVANCE_PATTERNS = [
  /continue to shipping/i,
  /continue to delivery/i,
  /weiter (?:zum|zur) versand/i,
  /weiter (?:zum|zur) lieferung/i,
  /continue to payment/i,
  /weiter (?:zum|zur) zahlung/i,
  /continue to review/i,
  /review order/i,
  /bestellung (?:prüfen|überprüfen|uberprüfen)/i
];

/**
 * Shopify checkout progression with an explicit final-purchase boundary.
 * It may advance between checkout sections, but final order controls are never
 * clicked unless the backend permission callback is true immediately before click.
 */
export class ShopifyCheckoutJourney {
  async isReadyForFinalSubmit(page: Page): Promise<boolean> {
    return Boolean(await this.findMatchingControl(page, FINAL_SUBMIT_PATTERNS));
  }

  async isOrderConfirmed(page: Page): Promise<boolean> {
    return (await observeCheckoutOutcome(page)).confirmed;
  }

  async advanceCheckout(page: Page): Promise<boolean> {
    const candidate = await this.findMatchingControl(page, SAFE_ADVANCE_PATTERNS, FINAL_SUBMIT_PATTERNS);
    if (!candidate) return false;

    try {
      await new GhostCursorUiInteractionHelper(page).click(candidate);
      await page.waitForLoadState("domcontentloaded", { timeout: 8_000 }).catch(() => undefined);
      return true;
    } catch {
      return false;
    }
  }

  /** Returns only whether the guarded irreversible click was dispatched. */
  async submitOrder(page: Page, canPurchase: () => boolean): Promise<boolean> {
    const candidate = await this.findMatchingControl(page, FINAL_SUBMIT_PATTERNS);
    if (!candidate) return false;

    // The permission check intentionally happens after all asynchronous discovery
    // and immediately before the final click. Default/failure state is blocked.
    if (!canPurchase()) return false;

    try {
      await new GhostCursorUiInteractionHelper(page).click(candidate);
      await page.waitForLoadState("domcontentloaded", { timeout: 10_000 }).catch(() => undefined);
      return true;
    } catch {
      return false;
    }
  }

  private async findMatchingControl(
    page: Page,
    patterns: RegExp[],
    excludedPatterns: RegExp[] = []
  ): Promise<Locator | undefined> {
    const controls = page.locator('button, input[type="submit"], [role="button"]');
    let count = 0;
    try {
      count = Math.min(100, await controls.count());
    } catch {
      return undefined;
    }

    for (let index = 0; index < count; index++) {
      const candidate = controls.nth(index);
      if (!await this.isActionable(candidate)) continue;
      const label = await this.controlLabel(candidate);
      if (!label || excludedPatterns.some(pattern => pattern.test(label))) continue;
      if (patterns.some(pattern => pattern.test(label))) return candidate;
    }
    return undefined;
  }

  private async isActionable(locator: Locator): Promise<boolean> {
    try {
      return await locator.isVisible({ timeout: 250 }) && await locator.isEnabled({ timeout: 250 });
    } catch {
      return false;
    }
  }

  private async controlLabel(locator: Locator): Promise<string> {
    try {
      return String(await locator.evaluate(element => {
        const input = element as HTMLInputElement;
        return [
          element.textContent ?? "",
          typeof input.value === "string" ? input.value : "",
          element.getAttribute("aria-label") ?? "",
          element.getAttribute("name") ?? ""
        ].join(" ").replace(/\s+/g, " ").trim();
      }));
    } catch {
      try {
        return (await locator.innerText({ timeout: 250 })).replace(/\s+/g, " ").trim();
      } catch {
        return "";
      }
    }
  }
}
