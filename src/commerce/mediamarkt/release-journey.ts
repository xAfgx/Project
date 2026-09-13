import type { Locator, Page } from "../../browser-worker/types";
import { observeCheckoutOutcome } from "../../browser-worker/checkout-outcome-observer";
import { GhostCursorUiInteractionHelper } from "../../browser-worker/ui-interaction-helper";
import type { CommerceShop } from "../platforms";
import { ProductMatcher } from "../../monitor/product-matcher";
import type { ProductObservation } from "../../monitor/models";
import type { ReleaseDiscoveryInput, ReleaseJourney } from "../release-discovery/release-journey";
import { ImapMailbox } from "../../mail/imap-mailbox";

export interface AccountRegistrationInput {
  email: string;
  password: string;
  firstName: string;
  lastName: string;
  salutation?: string;
  birthDate?: string;
  imap: { host: string; port: number; secure: boolean; user: string; password: string; mailbox?: string };
}

const ADD_TO_CART_TEXT = /in den warenkorb|in den einkaufswagen/i;
const CONTINUE_TESTID = '[data-test="checkout-continue-button"], [data-test="checkout-continue-desktop-enabled"], [data-test="checkout-continue-mobile-enabled"], [data-test="checkout-continue-button"] button';
const PAY_TEXT = /bezahlen|zahlungspflichtig|bestellung|kostenpflichtig/i;
const CONSENT_BUTTON_SELECTORS = [
  '[data-test="pwa-consent-layer-accept-all"]',
  '#pwa-consent-layer-accept-all-button',
  '#onetrust-accept-btn-handler',
  '[data-test="pwa-consent-layer-deny-all"]',
  '#pwa-consent-layer-deny-all-button',
  '#onetrust-reject-all-handler',
  '[data-test="pwa-consent-layer-save-settings"]',
  '[data-testid="uc-accept-all-button"]',
  '[data-testid="uc-deny-all-button"]',
  '[data-testid*="accept" i]',
  '[id*="accept-all" i]',
  '[class*="accept-all" i]'
];
const CONSENT_BUTTON_SELECTOR = CONSENT_BUTTON_SELECTORS.join(", ");
const CONSENT_VISIBLE_SELECTORS = [
  ...CONSENT_BUTTON_SELECTORS,
  '[data-test*="consent" i]',
  '[id*="consent" i]',
  '[class*="consent" i]',
  '[id*="cookie" i]',
  '[class*="cookie" i]',
  '#onetrust-banner-sdk',
  '#usercentrics-root'
];
const CONSENT_CLOSE_SELECTORS = [
  '[class*="consent" i] button[aria-label*="close" i]',
  '[id*="consent" i] button[aria-label*="close" i]',
  '[data-test*="consent" i] button[aria-label*="close" i]',
  '[class*="cookie" i] button[aria-label*="schließ" i]',
  '[id*="cookie" i] button[aria-label*="close" i]'
];
const CONSENT_CLOSE_SELECTOR = CONSENT_CLOSE_SELECTORS.join(", ");
const CONSENT_VISIBLE_SELECTOR = CONSENT_VISIBLE_SELECTORS.join(", ");

/**
 * Marketing/advert surfaces extracted from the real MediaMarkt markup. The bot
 * must never see or click them: newsletter subscription (incl. its `submitBtn`),
 * teasers, carousels, recommendations, sponsors and ad slots.
 */
export const EXCLUDED_MARKETING_SELECTOR = [
  '[data-test*="newsletter" i]',
  '[data-test="submitBtn"]',
  '[data-test*="teaser" i]',
  '[data-test*="carousel" i]',
  '[data-test*="recommendation" i]',
  '[data-test*="sponsor" i]',
  '[data-test*="advert" i]',
  'a[href*="guest-newsletter" i]',
  'a[href*="newsletter" i]',
  '[class*="newsletter" i]',
  '[id*="newsletter" i]',
  '[class*="teaser" i]',
  '[class*="advert" i]',
  '[class*="sponsor" i]'
].join(", ");

interface SearchHit {
  title: string;
  url: string;
  index: number;
}

export class MediaMarktReleaseJourney implements ReleaseJourney {
  private readonly matcher = new ProductMatcher();
  private readonly startedAt = Date.now();

  private log(message: string): void {
    process.stderr.write(`[JOURNEY] +${Date.now() - this.startedAt}ms ${message}\n`);
  }

  supports(shop: CommerceShop): boolean {
    try {
      const hostname = new URL(shop.baseUrl).hostname;
      return /(^|\.)mediamarkt\.(de|at|ch)$/i.test(hostname) || hostname === "localhost" || hostname === "127.0.0.1";
    } catch {
      return false;
    }
  }

  async discover(page: Page, shop: CommerceShop, input: ReleaseDiscoveryInput): Promise<ProductObservation | undefined> {
    const query = [input.productName, ...input.keywords].filter(Boolean).join(" ").trim();
    if (!await this.openSearch(page, shop, query || input.productName)) return undefined;
    // Enter can rarely open a suggestion product directly. Only enter this
    // branch when the freshly read URL proves that happened; probing the PDP
    // DOM on a confirmed search page creates a queue while suggestions settle.
    if (/\/de\/product\//i.test(await this.currentUrl(page))) {
      const url = await this.currentUrl(page);
      const title = await page.title().catch(() => "");
      if (await this.waitForAddToCart(page, 10_000)) {
        this.log(`discover suggestion-product "${title}" url=${url}`);
        return {
          shopId: shop.id,
          platform: shop.platform,
          title,
          url,
          available: true,
          observedAt: new Date(),
          attributes: { source: "mediamarkt-search-suggestion" }
        };
      }
    }
    if (!await this.waitForResults(page, 30_000)) return undefined;
    const hit = await this.bestResult(page, input);
    if (!hit) return undefined;
    this.log(`discover hit="${hit.title}" index=${hit.index}`);
    if (!await this.clickSearchResult(page, hit)) {
      throw new Error("MediaMarkt: Produkt-Treffer wurde erkannt, aber der Klick wurde nicht bestätigt.");
    }
    this.log(`discover product-click url=${await this.currentUrl(page)}`);
    if (!await this.onProductPage(page, 8_000)) return undefined;
    if (!await this.waitForAddToCart(page, 8_000)) {
      process.stderr.write("[JOURNEY] mediamarkt discover add-to-cart not ready\n");
      return undefined;
    }
    return {
      shopId: shop.id,
      platform: shop.platform,
      title: hit.title,
      url: hit.url,
      available: true,
      observedAt: new Date(),
      attributes: { source: "mediamarkt-search" }
    };
  }

  async addToCart(page: Page, _shop: CommerceShop, _product: ProductObservation): Promise<void> {
    // The PDP renders late; wait briefly for the document, then find the box.
    await page.waitForLoadState("domcontentloaded", { timeout: 5_000 }).catch(() => undefined);
    const add = await this.findAddToCartButton(page, 12_000);
    if (!add) {
      this.debug(page, "add-to-cart-missing", {
        url: await this.currentUrl(page),
        title: await page.title().catch(() => ""),
        pdpCount: await page.locator("#pdp-add-to-cart-button").count().catch(() => 0)
      });
      throw new Error("MediaMarkt: In den Warenkorb wurde nicht gefunden.");
    }
    // Live describe() is a multi-second evaluate on the busy page; the click
    // target was already resolved by the ranking above.
    // Baseline cart state: a retry is only allowed when nothing changed after
    // the click, so the page is never clicked twice without evidence.
    const baseline = await this.readCartCount(page);
    // Click the live control center natively. The ranked candidate is the one
    // inside the viewport (sticky bottom bar), so no scrolling is needed and no
    // blind click at off-screen coordinates can miss.
    // Dispatch exactly once. A worker timeout is ambiguous: the native click
    // may already have opened the mini-basket while the site's main thread is
    // still busy. Retrying at the old PDP coordinates can hit a recommendation
    // inside that popup and navigate back to another product.
    await add.click({ timeout: 10_000 }).catch(error => {
      this.debug(page, "add-to-cart-dispatch-timeout", {
        error: error instanceof Error ? error.message : String(error)
      });
    });
    if (await this.waitForCart(page, 10_000, baseline)) {
      this.log(`addToCart confirmed url=${await this.currentUrl(page)}`);
      return;
    }
    // The mini-basket modal IS the confirmation. Never click the page again
    // while it is open: a blind retry used to hit its recommendation tiles.
    if (await this.hasMiniBasket(page)) {
      this.log("addToCart mini-basket confirmed");
      return;
    }
    this.debug(page, "add-to-cart-no-confirm", {
      url: await this.currentUrl(page),
      miniBasket: await this.hasMiniBasket(page),
      baseline: baseline ?? null,
      cartCount: await this.readCartCount(page) ?? null
    });
    throw new Error("MediaMarkt: Warenkorb wurde nach dem einmaligen Add-to-Cart-Klick nicht bestätigt.");
  }

  /**
   * Cart confirmation from real state: cart/checkout URL, the shop's continue
   * control, the live mini-basket modal, or a header counter above baseline.
   */
  private async waitForCart(page: Page, timeoutMs: number, baselineCount?: number): Promise<boolean> {
    const deadline = Date.now() + Math.max(1, timeoutMs);
    do {
      if (await this.hasMiniBasket(page)) {
        this.debug(page, "cart-confirmed", { signal: "mini-basket" });
        return true;
      }
      if (await this.hasCartMarker(page)) {
        this.debug(page, "cart-confirmed", { signal: "cart-marker" });
        return true;
      }
      const count = await this.readCartCount(page);
      if (count !== undefined && (baselineCount === undefined ? count > 0 : count > baselineCount)) {
        this.debug(page, "cart-confirmed", { signal: "counter", count, baselineCount: baselineCount ?? null });
        return true;
      }
      if (/\/cart|\/checkout/i.test(await this.currentUrl(page))) {
        this.debug(page, "cart-confirmed", { signal: "url" });
        return true;
      }
      await page.waitForTimeout(500).catch(() => undefined);
    } while (Date.now() < deadline);
    return false;
  }

  /**
   * Header cart counter (live: the "1" badge top right). Generic scan so a
   * keyword or layout change cannot break the confirmation; undefined means no
   * counter is rendered (yet), which keeps the other signals authoritative.
   */
  private async readCartCount(page: Page): Promise<number | undefined> {
    return page.evaluate(() => {
      const candidates = document.querySelectorAll(
        'a[href*="/cart" i], a[href*="warenkorb" i], [data-test*="cart" i], [data-test*="basket" i], [aria-label*="warenkorb" i], [aria-label*="cart" i]'
      );
      for (const element of Array.from(candidates)) {
        const rect = element.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0 || rect.top > 320) continue;
        const text = (element.textContent || "").replace(/\s+/g, " ").trim();
        const match = text.match(/(?:^|\D)(\d{1,3})(?:\D|$)/);
        if (match) return Number(match[1]);
      }
      return undefined;
    }).catch(() => undefined);
  }

  /**
   * Live add-to-cart opens a modal ("Produkt wurde zum Warenkorb hinzugefügt")
   * with a "Zum Warenkorb" link instead of navigating.
   */
  private async hasMiniBasket(page: Page): Promise<boolean> {
    const marker = page.locator(
      '[data-test="pdp-minibasket-headline-success"], [data-test="mms-router-link-mms-pre-checkout-modal-primary-button"]'
    ).first();
    // Live RPC latency easily exceeds 1s; a 400ms probe timed out and made the
    // open modal invisible to the flow.
    return marker.isVisible({ timeout: 1_500 }).catch(() => false);
  }

  async openCheckout(page: Page, _shop: CommerceShop): Promise<void> {
    // The live add-to-cart modal carries the "Zum Warenkorb" primary link;
    // follow it before the wizard loop (the modal blocks the page otherwise).
    const modalPrimary = page.locator('[data-test="mms-router-link-mms-pre-checkout-modal-primary-button"]').first();
    this.debug(page, "minibasket-primary", { selector: "mms-router-link-mms-pre-checkout-modal-primary-button" });
    await modalPrimary.click({ timeout: 10_000 }).catch(error => {
      this.debug(page, "minibasket-primary-timeout", {
        error: error instanceof Error ? error.message : String(error)
      });
    });
    // A missed native click leaves the mini-basket modal open and the flow then
    // searches for cart controls on the product page. Retry once through a
    // plain native center click before looking for the cart controls.
    if (await this.hasMiniBasket(page)) {
      this.debug(page, "minibasket-primary-retry");
      await this.clickControlCenter(page, modalPrimary, "minibasket-primary").catch(() => false);
      await page.waitForTimeout(400).catch(() => undefined);
    }
    const checkoutContinue = await this.findButton(page, CONTINUE_TESTID, 30_000);
    if (!checkoutContinue) {
      throw new Error("MediaMarkt: 'Zur Kasse gehen' wurde im Warenkorb nicht gefunden.");
    }
    await checkoutContinue.click({ timeout: 10_000 });

    // Account login and guest checkout share the next screen. Guest selection
    // must happen before semantic autofill, otherwise the login email field is
    // mistaken for the delivery email field.
    let guest = page.locator('[data-test="guest-checkout"]').first();
    await guest.waitFor({ state: "attached", timeout: 8_000 }).catch(() => undefined);
    if (!await guest.isVisible({ timeout: 600 }).catch(() => false)) {
      guest = page.locator("button, a")
        .filter({ hasText: /als gast fortfahren|weiter als gast|als gast bestellen|gastbestellung/i })
        .first();
      await guest.waitFor({ state: "attached", timeout: 5_000 }).catch(() => undefined);
    }
    if (!await guest.isVisible({ timeout: 1_000 }).catch(() => false) || await this.isNewsletterOrAd(guest)) {
      throw new Error("MediaMarkt: Gast-Checkout wurde nach 'Zur Kasse gehen' nicht angeboten.");
    }
    await guest.click({ timeout: 10_000 });
    // Park the pointer out of the content. After the click the page can scroll
    // and slide a newsletter "Abmelden" link under the stale cursor position,
    // which only looks like the bot is about to click it.
    await page.mouse.move(4, 4).catch(() => undefined);

    await page.waitForTimeout(500).catch(() => undefined);
    this.debug(page, "checkout-guest-confirmed");
    // Opening checkout and filling its wizard are separate phases. The direct
    // executor owns preparation through advanceCheckout(); live probes can now
    // stop safely as soon as the checkout landing page is confirmed.
  }

  async isReadyForFinalSubmit(page: Page, _shop: CommerceShop): Promise<boolean> {
    return Boolean(await this.findButton(page, PAY_TEXT, 1));
  }

  async isOrderConfirmed(page: Page, _shop: CommerceShop): Promise<boolean> {
    return (await observeCheckoutOutcome(page)).confirmed;
  }

  async advanceCheckout(page: Page, _shop: CommerceShop): Promise<boolean> {
    if (await this.hasPaymentFields(page)) return false;
    const next = await this.findButton(page, CONTINUE_TESTID, 2_000);
    if (!next) return false;
    const before = await this.currentUrl(page);
    await new GhostCursorUiInteractionHelper(page).scrollIntoView(next).catch(() => undefined);
    await page.waitForTimeout(350).catch(() => undefined);
    await next.click({ timeout: 8_000 }).catch(() => undefined);
    await this.waitForUrlChange(page, before, 6_000);
    return true;
  }

  /** Returns only whether the guarded irreversible click was dispatched. */
  async submitOrder(page: Page, _shop: CommerceShop, allowFinalPurchase: () => boolean): Promise<boolean> {
    const pay = await this.findButton(page, PAY_TEXT, 2_000);
    if (!pay) return false;
    if (!allowFinalPurchase()) return false;
    await pay.click({ timeout: 8_000 });
    await page.waitForLoadState("domcontentloaded", { timeout: 20_000 }).catch(() => undefined);
    return true;
  }

  private async openSearch(page: Page, shop: CommerceShop, query: string): Promise<boolean> {
    const already = /\/search/i.test(page.url()) && page.url().toLowerCase().includes(query.toLowerCase());
    if (already) return true;
    return this.searchFromStartPage(page, shop, query);
  }

  /**
   * Human search entry: open the storefront start page, type the query into the
   * shop's own search field and submit it through native input. No script is
   * injected; only trusted CDP input events reach the page.
   */
  private async searchFromStartPage(page: Page, shop: CommerceShop, query: string): Promise<boolean> {
    this.log(`search goto ${shop.baseUrl}`);
    try {
      await page.goto(shop.baseUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });
    } catch {
      process.stderr.write("[JOURNEY] mediamarkt search goto failed\n");
      return false;
    }
    this.log(`search start-page url=${page.url()}`);
    // Wait for the document load event before touching the search box. Typing
    // into the React storefront before hydration looks fine in the DOM, but the
    // app state stays empty and Enter does nothing. The short extra pause covers
    // the hydration burst that follows the load event on this storefront.
    await page.waitForLoadState("load", { timeout: 15_000 }).catch(() => undefined);
    await page.waitForTimeout(3_000).catch(() => undefined);
    // The storefront occasionally loads already scrolled to the footer
    // newsletter. Correct that before the consent layer is handled so the jump
    // is not visible during the rest of the flow.
    // Stored consent (`pwaconsent` cookie) means no banner, no scroll and no
    // waiting: check it natively through the cookie store, not the renderer.
    const cookies = await (page as unknown as { cookies?: () => Promise<Array<Record<string, unknown>>> })
      .cookies?.().catch(() => []) ?? [];
    const consentStored = cookies.some(cookie =>
      String(cookie["name"] ?? "") === "pwaconsent" && String(cookie["value"] ?? "").length > 0
    );
    if (!consentStored) {
      const consentDismissed = await this.waitForMediaMarktConsent(page, 2_000);
      // Only correct the scroll when a consent layer was actually dismissed.
      if (consentDismissed) {
        await this.keepStartPageAtTop(page, "after-consent");
      }
    }
    await this.maybeSolveChallenge(page);
    await page.waitForTimeout(200).catch(() => undefined);
    const field = await this.findSearchField(page, 15_000);
    if (!field) {
      const title = await page.title().catch(() => "");
      this.log(`search field not found url=${await this.currentUrl(page)} title="${title}"`);
      return false;
    }
    process.stderr.write("[JOURNEY] mediamarkt search field found; typing\n");
    // The focus step itself waits for the busy renderer, so there is no extra
    // attachment wait before it.
    try {
      await field.type(query, { interKeyDelayMinMs: 90, interKeyDelayMaxMs: 210, focusNoScroll: true });
    } catch {
      // Fallback: the same native per-character typing path without the
      // readiness wrapper (e.g. animated sticky headers).
      await field.type(query, { interKeyDelayMinMs: 90, interKeyDelayMaxMs: 210 }).catch(() => undefined);
      if (!(await field.inputValue().catch(() => "")).trim()) return false;
    }
    this.log(`search typed="${query}"`);
    this.debug(page, "search-typed", { query });
    const before = page.url();
    // Refocus the field with a native click (the suggestion layer can steal the
    // active element) and submit with Enter, as it worked before.
    await field.click({ timeout: 15_000 }).catch(() => undefined);
    await field.press("Enter", { timeout: 30_000, focus: false, submit: true }).catch(() => undefined);
    const url = await this.waitForCachedSearchUrl(page, before, 12_000);
    this.log(`search submitted url=${url}`);
    this.debug(page, "search-submitted", { url });
    const submitted = /\/search|\/product\//i.test(url) || url.toLowerCase().includes(query.toLowerCase());
    if (!submitted) {
      throw new Error("MediaMarkt: Suche wurde nach Eingabe + Enter nicht bestätigt; Startseiten-Retry abgebrochen, damit der Newsletter nicht wieder anvisiert wird.");
    }
    // The suggestion layer can survive the SPA navigation and monopolize the
    // page main thread. Escape closes that known post-submit state without a
    // pointer action or a click target.
    await field.press("Escape", { timeout: 3_000, focus: false }).catch(() => undefined);
    await page.waitForTimeout(250).catch(() => undefined);
    return true;
  }

  private async waitForCachedSearchUrl(page: Page, before: string, timeoutMs: number): Promise<string> {
    const deadline = Date.now() + Math.max(1, timeoutMs);
    do {
      const url = await this.currentUrl(page);
      if (/\/search|\/product\//i.test(url) || url !== before) return url;
      await page.waitForTimeout(150).catch(() => undefined);
    } while (Date.now() < deadline);
    return this.currentUrl(page);
  }

  /** Polls the live URL until it leaves the previous document or matches /search. */
  private async waitForSearchUrl(page: Page, before: string, timeoutMs: number): Promise<string> {
    const deadline = Date.now() + Math.max(1, timeoutMs);
    do {
      const url = await this.currentUrl(page);
      if (/\/search/i.test(url) || url !== before) return url;
      await page.waitForTimeout(250).catch(() => undefined);
    } while (Date.now() < deadline);
    return this.currentUrl(page);
  }

  /** Polls until the document URL changed (wizard step committed). */
  private async waitForUrlChange(page: Page, before: string, timeoutMs: number): Promise<string> {
    const deadline = Date.now() + Math.max(1, timeoutMs);
    do {
      const url = await this.currentUrl(page);
      if (url !== before) return url;
      await page.waitForTimeout(500).catch(() => undefined);
    } while (Date.now() < deadline);
    return this.currentUrl(page);
  }

  private async findSearchField(page: Page, _timeoutMs: number): Promise<Locator | undefined> {
    // Live-proven MediaMarkt header search id. No JS attachment probe here: the
    // live storefront blocks its renderer for seconds while hydrating, and the
    // caller's native focus+type retry already waits for the field to exist.
    return page.locator("#search-form").first();
  }

  /**
   * True when another element (consent overlay, ad layer) sits on top of the
   * control. Read-only geometry check; no page script is injected.
   */
  private async isCovered(locator: Locator): Promise<boolean> {
    return locator.evaluate((element: Element) => {
      const rect = element.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return false;
      const x = rect.left + rect.width / 2;
      const y = rect.top + Math.min(rect.height / 2, 20);
      const top = document.elementFromPoint(x, y);
      return Boolean(top && top !== element && !element.contains(top));
    }).catch(() => false);
  }

  private async isInViewport(page: Page, locator: Locator): Promise<boolean> {
    const viewport = await (page as unknown as { viewportSize?: () => Promise<{ width: number; height: number }> })
      .viewportSize?.()
      .catch(() => ({ width: 0, height: 0 })) ?? { width: 0, height: 0 };
    const viewportHeight = viewport.height > 0 ? viewport.height : 800;
    const box = await locator.boundingBox().catch(() => null);
    return Boolean(box && box.y >= -4 && box.y + box.height <= viewportHeight + 4);
  }

  private async bringIntoView(page: Page, locator: Locator): Promise<boolean> {
    await new GhostCursorUiInteractionHelper(page).scrollIntoView(locator).catch(() => undefined);
    await page.waitForTimeout(150).catch(() => undefined);
    return this.isInViewport(page, locator);
  }

  private async keepStartPageAtTop(page: Page, stage: string): Promise<void> {
    for (let attempt = 0; attempt < 3; attempt++) {
      const bodyBox = await page.locator("body").first().boundingBox().catch(() => null);
      const bodyY = bodyBox ? Math.round(bodyBox.y) : 0;
      this.debug(page, "startpage-top-lock", { stage, attempt, bodyY });
      if (bodyY > -120) return;
      await page.mouse.wheel(0, -7200).catch(() => undefined);
      await page.waitForTimeout(180).catch(() => undefined);
    }
  }

  private async assertNotFooterJump(page: Page, stage: string): Promise<void> {
    const body = page.locator("body").first();
    const box = await body.boundingBox().catch(() => null);
    this.debug(page, "viewport-check", { stage, bodyY: box ? Math.round(box.y) : null });
    if (box && box.y < -600) {
      throw new Error(`MediaMarkt: Cookie-Klick sprang in "${stage}" in den Newsletter-/Footer-Bereich; Suche wird nicht gestartet.`);
    }
  }

  /**
   * Main document first; a shop search field is a top-level control. The frame
   * list is capped so ad/consent iframes cannot turn one lookup into a scan of
   * every frame on the page.
   */
  private fieldHosts(page: Page): Array<{ locator(selector: string): Locator }> {
    const frames = typeof page.frames === "function" ? page.frames() : [];
    return [page, ...frames.slice(1, 6)];
  }

  /** Direct SeleniumBase vision/grid captcha poll (only for visible challenges). */
  private async solveChallenge(page: Page): Promise<void> {
    const solver = (page as unknown as { solveCaptcha?: () => Promise<boolean> }).solveCaptcha;
    if (typeof solver !== "function") return;
    await solver.call(page).catch(() => false);
  }

  /**
   * SeleniumBase vision/grid captcha poll, gated on a visibly rendered
   * challenge widget. Script/iframe leftovers (DataDome references in the page
   * source) must not trigger the heavy vision path on normal pages.
   */
  private async maybeSolveChallenge(page: Page): Promise<void> {
    const selector = [
      'iframe[src*="recaptcha"]',
      'iframe[src*="hcaptcha"]',
      'iframe[src*="turnstile"]',
      'iframe[src*="captcha-delivery"]',
      '.captcha-grid',
      '#captcha__frame',
      '[data-dd-captcha-container]',
      '#challenge-running'
    ].join(", ");
    // One combined probe instead of eight separate ones: each live probe costs
    // 250ms+ and they all queued behind the busy storefront renderer.
    if (!await page.locator(selector).first().isVisible({ timeout: 400 }).catch(() => false)) return;
    this.log("challenge visible");
    await this.solveChallenge(page);
  }

  /**
   * Hard exclusion for MediaMarkt newsletter/ad controls. The storefront's
   * sticky newsletter block uses `data-test=mms-newsletter-subscription` and
   * `href=.../guest-newsletter`; it must never be clicked or scrolled into view.
   */
  private async isNewsletterOrAd(candidate: Locator): Promise<boolean> {
    return candidate.evaluate((element: Element, excluded: string) => {
      if (element.closest(excluded)) return true;
      const text = [
        element.textContent || "",
        element.getAttribute("aria-label") || "",
        element.getAttribute("href") || "",
        element.getAttribute("data-test") || ""
      ].join(" ").toLowerCase();
      return /newsletter|abonnieren|guest-newsletter/.test(text);
    }, EXCLUDED_MARKETING_SELECTOR).catch(() => false);
  }

  // TEMPORARY LIVE DEBUG (remove after live validation): records every click
  // target and page transition into a ring buffer on the page object, which the
  // executor mirrors into the task data so it persists in ares.sqlite.
  private debug(page: Page, action: string, detail: Record<string, unknown> = {}): void {
    try {
      const buffer = (page["mediamarktDebug"] as Array<Record<string, unknown>> | undefined) ?? [];
      buffer.push({ at: new Date().toISOString(), action, ...detail });
      if (buffer.length > 200) buffer.splice(0, buffer.length - 200);
      page["mediamarktDebug"] = buffer;
    } catch {
      // Debug must never break the flow.
    }
  }

  private async describe(locator: Locator): Promise<Record<string, unknown>> {
    return locator.evaluate((element: Element, excluded: string) => {
      const rect = element.getBoundingClientRect();
      return {
        tag: element.tagName.toLowerCase(),
        text: (element.textContent || "").replace(/\s+/g, " ").trim().slice(0, 90),
        test: element.getAttribute("data-test") || "",
        id: (element as HTMLElement).id || "",
        cls: String((element as HTMLElement).className || "").slice(0, 90),
        aria: element.getAttribute("aria-label") || "",
        href: element.getAttribute("href") || "",
        marketing: Boolean(element.closest(excluded)),
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        w: Math.round(rect.width),
        h: Math.round(rect.height),
        scrollY: Math.round(window.scrollY)
      };
    }, EXCLUDED_MARKETING_SELECTOR).catch(() => ({}));
  }

  private async ensureConsentCleared(page: Page, timeoutMs: number, stage: string): Promise<void> {
    const deadline = Date.now() + Math.max(1, timeoutMs);
    let observed = false;
    let emptyRounds = 0;
    do {
      // Native locator click only (no page-side JS). dismissConsent resolves the
      // pwa-consent-layer accept/deny button through its real data-test.
      const clicked = await this.dismissConsent(page);
      if (clicked) {
        observed = true;
        emptyRounds = 0;
        this.debug(page, "consent-clicked", { stage });
        await page.waitForTimeout(300).catch(() => undefined);
        continue;
      }
      // The banner can arrive a beat after the document loads. Give it a short,
      // bounded window, then stop polling the moment it is clearly absent so a
      // cookie-free page never stalls the search start.
      emptyRounds += 1;
      if (emptyRounds >= 4) {
        this.debug(page, "consent-clear", { stage, observed });
        if (await this.hasVisibleConsent(page)) {
          throw new Error(`MediaMarkt: Cookie-Banner ist in "${stage}" noch sichtbar; Navigation/Suche bleibt gestoppt.`);
        }
        return;
      }
      await page.waitForTimeout(250).catch(() => undefined);
    } while (Date.now() < deadline);

    if (await this.hasVisibleConsent(page)) {
      throw new Error(`MediaMarkt: Cookie-Banner ist in "${stage}" noch sichtbar; Navigation/Suche bleibt gestoppt.`);
    }
    this.debug(page, "consent-clear", { stage, observed });
  }

  private async hasVisibleConsent(page: Page): Promise<boolean> {
    const hosts = [page, ...page.frames().slice(1, 3)];
    for (const host of hosts) {
      for (const selector of CONSENT_VISIBLE_SELECTORS) {
        const candidate = host.locator(selector).first();
        if (!await candidate.isVisible({ timeout: 150 }).catch(() => false)) continue;
        if (await this.isNewsletterOrAd(candidate)) continue;
        return true;
      }
    }
    return false;
  }

  /** Best-effort overlay/consent dismissal through native clicks. */
  private async dismissConsent(page: Page): Promise<boolean> {
    // MediaMarkt privacy layer + OneTrust (Pokémon Center-style CMP): accept
    // first, then the privacy-safe "only necessary"/"reject all" controls.
    const hosts = [page, ...page.frames().slice(1, 3)];
    for (const host of hosts) {
      const candidate = await this.firstVisibleFromSelectors(host, CONSENT_BUTTON_SELECTORS, 250);
      if (!candidate) continue;
      if (await this.isNewsletterOrAd(candidate)) continue;
      this.debug(page, "consent-click", await this.describe(candidate));
      const clicked = await this.clickVisibleCenter(page, candidate, "consent-click");
      if (!clicked) continue;
      await page.waitForTimeout(300).catch(() => undefined);
      return true;
    }
    // Generic fallback (other CMPs): one role-regex lookup instead of a
    // per-pattern scan: the old loop cost ~15s across frames on every page.
    const acceptName = /akzeptieren|zulassen|notwendige|erforderliche|essenzielle|zustimmen|einverstanden|accept all|only necessary/i;
    for (const host of hosts) {
      const accept = host.getByRole("button", { name: acceptName }).first();
      if (await accept.isVisible({ timeout: 180 }).catch(() => false)) {
        if (await this.isNewsletterOrAd(accept)) continue;
        this.debug(page, "consent-click", await this.describe(accept));
        const clicked = await this.clickVisibleCenter(page, accept, "consent-click");
        if (!clicked) continue;
        await page.waitForTimeout(300).catch(() => undefined);
        return true;
      }
    }
    // Close controls only inside real consent/cookie containers.
    for (const host of hosts) {
      const close = await this.firstVisibleFromSelectors(host, CONSENT_CLOSE_SELECTORS, 180);
      if (close) {
        if (await this.isNewsletterOrAd(close)) continue;
        this.debug(page, "consent-close", await this.describe(close));
        const clicked = await this.clickVisibleCenter(page, close, "consent-close");
        if (!clicked) continue;
        await page.waitForTimeout(300).catch(() => undefined);
        return true;
      }
    }
    return false;
  }

  private async waitForMediaMarktConsent(page: Page, timeoutMs: number): Promise<boolean> {
    const selector = [
      '#pwa-consent-layer-accept-all-button',
      '[data-test="pwa-consent-layer-accept-all"]',
      '#pwa-consent-layer-deny-all-button',
      '[data-test="pwa-consent-layer-deny-all"]'
    ].join(", ");
    // One bounded wait that polls inside the worker. Repeated short probes from
    // here stacked behind the busy renderer and doubled the consent phase.
    const candidate = page.locator(selector).first();
    const appeared = await candidate.waitFor({ state: "visible", timeout: timeoutMs })
      .then(() => true)
      .catch(() => false);
    if (!appeared) return false;
    if (await this.isNewsletterOrAd(candidate)) return false;
    await candidate.click({ timeout: 3_000 }).catch(error => {
      this.debug(page, "consent-mediamarkt-click-timeout", {
        error: error instanceof Error ? error.message : String(error)
      });
    });
    await page.waitForTimeout(250).catch(() => undefined);
    return true;
  }

  private async clickVisibleCenter(page: Page, locator: Locator, label: string): Promise<boolean> {
    const box = await locator.boundingBox().catch(() => null);
    if (!box || box.width <= 0 || box.height <= 0) return false;
    const x = box.x + box.width / 2;
    const y = box.y + box.height / 2;
    if (x < 0 || y < 0 || x > 1800 || y > 1200) {
      this.debug(page, `${label}-offscreen`, { x: Math.round(x), y: Math.round(y), box });
      return false;
    }
    return page.mouse.click(x, y).then(() => true).catch(error => {
      this.debug(page, `${label}-mouse-failed`, {
        x: Math.round(x),
        y: Math.round(y),
        error: error instanceof Error ? error.message : String(error)
      });
      return false;
    });
  }

  private async firstVisibleFromSelectors(
    host: { locator(selector: string): Locator },
    selectors: readonly string[],
    timeout = 300
  ): Promise<Locator | undefined> {
    for (const selector of selectors) {
      const candidate = host.locator(selector).first();
      if (await candidate.isVisible({ timeout }).catch(() => false)) return candidate;
    }
    return undefined;
  }

  private async firstVisibleCandidate(locator: Locator, max = 6, timeout = 300): Promise<Locator | undefined> {
    const count = Math.min(await locator.count().catch(() => 0), max);
    for (let index = 0; index < count; index++) {
      const candidate = locator.nth(index);
      if (await candidate.isVisible({ timeout }).catch(() => false)) return candidate;
    }
    return undefined;
  }

  private async waitForResults(page: Page, timeoutMs: number): Promise<boolean> {
    // One bounded worker request. Repeating short count() probes is unsafe here:
    // their Node timeouts do not cancel already-running CDP work and therefore
    // build a queue while MediaMarkt's suggestion renderer owns the main thread.
    return page.locator('a[href*="/de/product/"]').first()
      .waitFor({ state: "attached", timeout: timeoutMs })
      .then(() => true)
      .catch(() => false);
  }

  private async bestResult(page: Page, input: ReleaseDiscoveryInput): Promise<SearchHit | undefined> {
    const anchors = page.locator('a[href*="/de/product/"]');
    const hits = await anchors.evaluateAll((elements, excluded): SearchHit[] => {
      const seen = new Set<string>();
      const out: SearchHit[] = [];
      for (let index = 0; index < elements.length; index++) {
        // Never pick a product link that lives inside a teaser/carousel/ad.
        if (excluded && elements[index].closest(excluded)) continue;
        const href = elements[index].getAttribute("href") || "";
        let url = href;
        try { url = new URL(href, location.href).toString(); } catch { url = href; }
        if (!url || seen.has(url)) continue;
        seen.add(url);
        const title = (elements[index].textContent || "").replace(/\s+/g, " ").trim();
        out.push({ title, url, index });
      }
      return out;
    }, EXCLUDED_MARKETING_SELECTOR).catch(() => [] as SearchHit[]);
    if (!hits.length) return undefined;

    const criteria = {
      searchTerm: [input.productName, ...input.keywords].filter(Boolean).join(" "),
      requireAvailable: false,
      minimumScore: 0.35
    };
    let best: { hit: SearchHit; score: number } | undefined;
    for (const hit of hits) {
      const observation: ProductObservation = {
        shopId: "",
        platform: "mediamarkt",
        title: hit.title,
        url: hit.url,
        available: true,
        observedAt: new Date()
      };
      const match = this.matcher.match(observation, criteria);
      if (match.matched && (!best || match.score > best.score)) best = { hit, score: match.score };
    }
    return best?.hit;
  }

  private async clickSearchResult(page: Page, hit: SearchHit): Promise<boolean> {
    const anchor = page.locator('a[href*="/de/product/"]').nth(hit.index);
    if (await this.isNewsletterOrAd(anchor)) return false;
    this.debug(page, "product-click", { hit: hit.url, title: hit.title, index: hit.index });
    let clicked = await anchor.click({ timeout: 8_000 })
      .then(() => true)
      .catch(error => {
        this.debug(page, "product-click-failed", {
          hit: hit.url,
          index: hit.index,
          error: error instanceof Error ? error.message : String(error)
        });
        return false;
      });
    if (!clicked) {
      // Native center click fallback: the locator click can miss while the
      // result grid is still settling.
      clicked = await this.clickControlCenter(page, anchor, "product-click").catch(() => false);
    }
    if (!clicked) return false;
    return this.waitForUrl(page, url => /\/de\/product\//i.test(url), 10_000);
  }

  private async waitForAddToCart(page: Page, timeoutMs: number): Promise<boolean> {
    return Boolean(await this.findAddToCartButton(page, timeoutMs));
  }

  /**
   * The product-detail add-to-cart control. Search result cards also render
   * quick-add buttons with the same label, so the PDP selectors win and the
   * generic text match is only accepted once the product document is open.
   * The live PDP renders the same control twice (regular buy box below the
   * fold + sticky bottom bar), so candidates are ranked by clickability:
   * inside the viewport first, then uncovered, then sticky/fixed as tiebreak.
   */
  private async findAddToCartButton(page: Page, timeoutMs: number): Promise<Locator | undefined> {
    const deadline = Date.now() + Math.max(1, timeoutMs);
    do {
      const ranked = await this.rankAddToCartControls(page);
      if (ranked.length > 0) {
        this.debug(page, "add-to-cart-candidates", { candidates: ranked.map(entry => entry.info) });
        return ranked[0].locator;
      }
      if (/\/de\/product\//i.test(await this.currentUrl(page))) {
        const textAdd = await this.firstVisible(page.locator("button").filter({ hasText: ADD_TO_CART_TEXT }));
        if (textAdd && !await this.isNewsletterOrAd(textAdd)) return textAdd;
      }
      await page.waitForTimeout(250).catch(() => undefined);
    } while (Date.now() < deadline);
    return undefined;
  }

  /**
   * Rank every visible add-to-cart control by what can actually be clicked.
   * One evaluate per candidate keeps the live RPC count low (the page is slow):
   * it returns box, visibility, enabled state, marketing exclusion and whether
   * the element center is covered by an overlay.
   */
  private async rankAddToCartControls(page: Page): Promise<Array<{ locator: Locator; info: Record<string, unknown> }>> {
    // One evaluateAll instead of viewportSize + count + one evaluate per
    // candidate: on the busy live page each of those round-trips costs 2-8s,
    // which is what made the product page feel stuck.
    const base = page.locator('#pdp-add-to-cart-button, button[data-test*="cofr-add-to-basket" i]');
    const entries = await base.evaluateAll((elements: Element[], excluded: string) => {
      const viewportHeight = window.innerHeight || 800;
      const viewportWidth = window.innerWidth || 1280;
      return elements.map((element, index) => {
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        const text = [
          element.textContent || "",
          element.getAttribute("aria-label") || "",
          element.getAttribute("data-test") || ""
        ].join(" ").toLowerCase();
        const x = rect.left + rect.width / 2;
        const y = rect.top + Math.min(rect.height / 2, 20);
        const top = document.elementFromPoint(x, y);
        const inView = rect.y >= -4 && rect.y + rect.height <= viewportHeight + 4
          && rect.x >= -4 && rect.x + rect.width <= viewportWidth + 4;
        const sticky = style.position === "fixed" || style.position === "sticky";
        const covered = Boolean(top && top !== element && !element.contains(top));
        return {
          index,
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
          visible: rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) > 0,
          enabled: !(element as HTMLButtonElement).disabled && element.getAttribute("aria-disabled") !== "true",
          marketing: Boolean(element.closest(excluded)) || /newsletter|abonnieren|guest-newsletter/.test(text),
          covered,
          position: style.position,
          inView,
          sticky,
          score: (inView ? 4 : 0) + (covered ? 0 : 2) + (sticky ? 1 : 0)
        };
      }).filter(info => info.visible && info.enabled && !info.marketing);
    }, EXCLUDED_MARKETING_SELECTOR).catch(() => [] as Array<Record<string, unknown>>);

    const seen = new Set<string>();
    const ranked: Array<{ locator: Locator; info: Record<string, unknown> }> = [];
    for (const entry of entries) {
      const key = `${entry["x"]}:${entry["y"]}:${entry["width"]}:${entry["height"]}`;
      if (seen.has(key)) continue;
      seen.add(key);
      ranked.push({ locator: base.nth(Number(entry["index"] ?? 0)), info: entry });
    }
    ranked.sort((left, right) => Number(right.info["score"] ?? 0) - Number(left.info["score"] ?? 0));
    return ranked;
  }

  /**
   * Native click on the live control center. The sticky bottom bar sits inside
   * the viewport, so no scroll is needed and no JS scrollIntoView runs. Covered
   * or off-viewport candidates are never clicked: an overlay (e.g. the
   * mini-basket modal) would otherwise receive a click on a recommendation.
   */
  private async clickControlCenter(page: Page, locator: Locator, label = "add-to-cart"): Promise<boolean> {
    const box = await locator.boundingBox().catch(() => null);
    if (!box || box.width <= 0 || box.height <= 0) return false;
    const viewport = await (page as unknown as { viewportSize?: () => Promise<{ width: number; height: number }> })
      .viewportSize?.()
      .catch(() => ({ width: 0, height: 0 })) ?? { width: 0, height: 0 };
    const x = box.x + box.width / 2;
    const y = box.y + box.height / 2;
    if (viewport.height > 0 && (y < 0 || y > viewport.height)) return false;
    if (await this.isCovered(locator)) {
      this.debug(page, `${label}-covered`, { x: Math.round(x), y: Math.round(y), box });
      return false;
    }
    this.debug(page, `${label}-mouse`, { x: Math.round(x), y: Math.round(y), box });
    return page.mouse.click(x, y).then(() => true).catch(error => {
      this.debug(page, `${label}-mouse-failed`, {
        x: Math.round(x),
        y: Math.round(y),
        error: error instanceof Error ? error.message : String(error)
      });
      return false;
    });
  }

  /** First visible + enabled element of a locator (responsive duplicates). */
  private async firstVisible(locator: Locator, max = 6): Promise<Locator | undefined> {
    const count = Math.min(await locator.count().catch(() => 0), max);
    for (let index = 0; index < count; index++) {
      const candidate = locator.nth(index);
      // Live RPC latency easily exceeds 1s; shorter probes timed out and made
      // real buttons look "not visible" on the live storefront.
      if (await candidate.isVisible({ timeout: 1_500 }).catch(() => false) && await candidate.isEnabled({ timeout: 1_500 }).catch(() => false)) return candidate;
    }
    return undefined;
  }

  /** Refreshes the cached page URL through a cheap locator round-trip. */
  private async currentUrl(page: Page): Promise<string> {
    const refresh = (page as Page & { refreshCurrentUrl?: () => Promise<string> }).refreshCurrentUrl;
    if (typeof refresh === "function") return refresh.call(page).catch(() => page.url());
    await page.locator("html").count().catch(() => 0);
    return page.url();
  }

  /**
   * One long readiness call after a navigation. The live page's CDP session can
   * be briefly stale after a document swap; short probes sent immediately all
   * time out and pile up in the worker queue, which stalled the flow for tens
   * of seconds. Waiting once with a long timeout lets the session recover.
   */
  private async settleAfterNavigation(page: Page): Promise<void> {
    await page.waitForLoadState("domcontentloaded", { timeout: 30_000 }).catch(() => undefined);
    await page.waitForTimeout(500).catch(() => undefined);
  }

  private async onProductPage(page: Page, timeoutMs: number): Promise<boolean> {
    const deadline = Date.now() + Math.max(1, timeoutMs);
    do {
      if (/\/de\/product\//i.test(await this.currentUrl(page))) return true;
      if (await page.locator("#pdp-add-to-cart-button").isVisible({ timeout: 1_000 }).catch(() => false)) return true;
      await page.waitForTimeout(300).catch(() => undefined);
    } while (Date.now() < deadline);
    return false;
  }

  private async hasCartMarker(page: Page): Promise<boolean> {
    const marker = page.locator('[data-test="checkout-continue-button"]').first();
    return marker.isVisible({ timeout: 1_500 }).catch(() => false);
  }

  private async hasPaymentFields(page: Page): Promise<boolean> {
    const field = page.locator("#cardNumber, input[autocomplete='cc-number']").first();
    return field.isVisible({ timeout: 2_500 }).catch(() => false);
  }

  private async waitForUrl(page: Page, predicate: (url: string) => boolean, timeoutMs: number): Promise<boolean> {
    const deadline = Date.now() + timeoutMs;
    do {
      if (predicate(await this.currentUrl(page))) return true;
      await page.waitForTimeout(250).catch(() => undefined);
    } while (Date.now() < deadline);
    return predicate(await this.currentUrl(page));
  }

  private async findButton(page: Page, include: RegExp | string, timeoutMs: number): Promise<Locator | undefined> {
    const deadline = Date.now() + Math.max(1, timeoutMs);
    do {
      const base = typeof include === "string"
        ? page.locator(include)
        : page.locator("button, a").filter({ hasText: include });
      const candidate = await this.pickClickable(page, base);
      if (candidate && !await this.isNewsletterOrAd(candidate)) return candidate;
      await page.waitForTimeout(500).catch(() => undefined);
    } while (Date.now() < deadline);
    return undefined;
  }

  /**
   * First really clickable candidate of a locator. Responsive duplicates exist
   * (the checkout continue button renders desktop and mobile variants), and the
   * first DOM match can measure 0x0, which made the wizard click miss. One
   * evaluate per candidate returns box, visibility, enabled state and overlay
   * coverage; in-view uncovered candidates win, then any real fallback.
   */
  private async pickClickable(page: Page, base: Locator, max = 6): Promise<Locator | undefined> {
    const viewport = await (page as unknown as { viewportSize?: () => Promise<{ width: number; height: number }> })
      .viewportSize?.()
      .catch(() => ({ width: 0, height: 0 })) ?? { width: 0, height: 0 };
    const viewportHeight = viewport.height > 0 ? viewport.height : 800;
    const count = Math.min(await base.count().catch(() => 0), max);
    let fallback: Locator | undefined;
    let container: Locator | undefined;
    for (let index = 0; index < count; index++) {
      const candidate = base.nth(index);
      const info = await candidate.evaluate((element: Element) => {
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        const x = rect.left + rect.width / 2;
        const y = rect.top + Math.min(rect.height / 2, 20);
        const top = document.elementFromPoint(x, y);
        const tag = element.tagName.toLowerCase();
        const role = element.getAttribute("role") || "";
        return {
          y: Math.round(rect.y),
          height: Math.round(rect.height),
          visible: rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) > 0,
          enabled: !(element as HTMLButtonElement).disabled && element.getAttribute("aria-disabled") !== "true",
          covered: Boolean(top && top !== element && !element.contains(top)),
          // A real control beats its own wrapper. MediaMarkt's desktop checkout
          // continue is a container div whose center is the price text, so
          // clicking the wrapper silently does nothing.
          interactive: tag === "button" || tag === "a" || tag === "input" || role === "button" || role === "link"
        };
      }).catch(() => null);
      if (!info || !info.visible || !info.enabled) continue;
      const inView = !info.covered && info.y >= -4 && info.y + info.height <= viewportHeight + 4;
      if (inView) {
        if (info.interactive) return candidate;
        if (!container) container = candidate;
        continue;
      }
      if (!fallback) fallback = candidate;
    }
    return container ?? fallback;
  }

  /**
   * MediaMarkt account registration: email -> 6-digit code from the mailbox via
   * IMAP -> personal data. Isolated from the checkout flow; only called for
   * registration tasks and never from discover/addToCart/openCheckout.
   */
  async registerAccount(page: Page, shop: CommerceShop, input: AccountRegistrationInput, signal?: AbortSignal): Promise<{ status: "confirmed" | "failed"; message: string }> {
    const aborted = (): boolean => signal?.aborted === true;
    const base = shop.baseUrl.replace(/\/$/, "");
    const registrationUrl = `${base}/de/myaccount/auth/registration`;
    this.log(`register goto ${registrationUrl}`);
    try {
      await page.goto(registrationUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });
    } catch {
      return { status: "failed", message: "Registrierungsseite konnte nicht geladen werden." };
    }
    if (aborted()) return { status: "failed", message: "Abgebrochen." };
    await page.waitForLoadState("load", { timeout: 15_000 }).catch(() => undefined);
    await page.waitForTimeout(2_000).catch(() => undefined);

    // Step 1: email.
    const emailField = page.locator('#email, input[type="email"], input[name*="email" i]').first();
    await emailField.waitFor({ state: "visible", timeout: 20_000 }).catch(() => undefined);
    if (aborted()) return { status: "failed", message: "Abgebrochen." };
    await emailField.type(input.email, { interKeyDelayMinMs: 60, interKeyDelayMaxMs: 140, clear: true }).catch(() => undefined);
    await page.locator('[data-test="mms-myacc-registration-email-form-submit"]').first().click({ timeout: 10_000 }).catch(() => undefined);
    this.log("register email submitted");

    // Step 2: wait for the verification code in the mailbox.
    const mailbox = new ImapMailbox({
      id: "registration",
      name: "registration",
      host: input.imap.host,
      port: input.imap.port,
      secure: input.imap.secure,
      user: input.imap.user,
      password: input.imap.password,
      mailbox: input.imap.mailbox
    });
    const code = await mailbox.waitForCode({ since: new Date(Date.now() - 5 * 60_000), limit: 5 }, 180_000, 4_000, signal)
      .catch(error => {
        this.log(`register imap error ${error instanceof Error ? error.message : String(error)}`);
        return undefined;
      });
    if (aborted()) return { status: "failed", message: "Abgebrochen." };
    if (!code) {
      const probe = await mailbox.fetchLatest({ since: new Date(Date.now() - 10 * 60_000), limit: 5 }).catch(() => []);
      this.log(`register imap ${input.imap.host}:${input.imap.port} user=${input.imap.user} mailbox=${input.imap.mailbox ?? "INBOX"} probe=${probe.length} subjects=${JSON.stringify(probe.map(message => message.subject).slice(0, 5))}`);
      return { status: "failed", message: "Kein 6-stelliger Bestätigungscode per IMAP gefunden." };
    }
    this.log(`register code=${code}`);

    const digits = code.slice(0, 6).split("");
    for (let index = 0; index < 6; index++) {
      const codeInput = page.locator(`#code-input-${index}`).first();
      await codeInput.type(digits[index] ?? "", { interKeyDelayMinMs: 30, interKeyDelayMaxMs: 80 }).catch(() => undefined);
    }
    await page.locator('[data-test="mms-myacc-registration-code-form-submit"]').first().click({ timeout: 10_000 }).catch(() => undefined);
    await page.waitForTimeout(4_000).catch(() => undefined);

    // Step 3: personal data (best effort; field dump keeps this debuggable).
    const visibleFields = await page.locator("input, select").evaluateAll((elements: Element[]) => elements
      .filter(element => {
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      })
      .map(element => ({
        tag: element.tagName.toLowerCase(),
        type: element.getAttribute("type") || "",
        id: (element as HTMLElement).id || "",
        name: element.getAttribute("name") || "",
        placeholder: element.getAttribute("placeholder") || "",
        test: element.getAttribute("data-test") || ""
      }))
    ).catch(() => []);
    process.stderr.write(`[JOURNEY] mediamarkt register-step3-fields ${JSON.stringify(visibleFields)}\n`);

    const fill = async (selector: string, value: string): Promise<void> => {
      if (!value) return;
      const field = page.locator(selector).first();
      if (!await field.isVisible({ timeout: 2_000 }).catch(() => false)) return;
      await field.type(value, { interKeyDelayMinMs: 50, interKeyDelayMaxMs: 120, clear: true }).catch(() => undefined);
    };

    // Salutation (Herr/Frau) is a radio group; the value/id may be German or
    // English depending on the form revision.
    const salutationRaw = String(input.salutation ?? "").trim().toLowerCase();
    if (salutationRaw) {
      const tokens = salutationRaw.startsWith("fr")
        ? ["frau", "female"]
        : salutationRaw.startsWith("h")
          ? ["herr", "male"]
          : [salutationRaw];
      for (const token of tokens) {
        const radio = page.locator(`input[type="radio"][value*="${token}" i], input[type="radio"][id*="${token}" i]`).first();
        if (await radio.isVisible({ timeout: 1_500 }).catch(() => false)) {
          await radio.click({ timeout: 5_000 }).catch(() => undefined);
          break;
        }
      }
    }
    await fill('input[name*="firstName" i], input[autocomplete="given-name"], #firstName', input.firstName);
    await fill('input[name*="lastName" i], input[autocomplete="family-name"], #lastName', input.lastName);
    await fill('input[name*="birth" i], input[id*="birth" i], input[autocomplete="bday"], input[type="date"]', input.birthDate ?? "");
    await fill('input[type="password"]', input.password);
    const submit = page.locator('[data-test*="registration" i][data-test*="submit" i], button[type="submit"]').first();
    await submit.click({ timeout: 10_000 }).catch(() => undefined);
    await page.waitForTimeout(6_000).catch(() => undefined);

    const url = await this.currentUrl(page);
    const confirmed = !/registration|auth\/login/i.test(url);
    return {
      status: confirmed ? "confirmed" : "failed",
      message: confirmed ? `Registrierung abgeschlossen (${url})` : `Registrierung nicht bestätigt (${url})`
    };
  }
}
