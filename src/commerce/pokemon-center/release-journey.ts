import type { Locator, Page } from "../../browser-worker/types";
import { observeCheckoutOutcome } from "../../browser-worker/checkout-outcome-observer";
import { GhostCursorUiInteractionHelper } from "../../browser-worker/ui-interaction-helper";
import type { CommerceShop } from "../platforms";
import { ProductMatcher } from "../../monitor/product-matcher";
import type { ProductObservation } from "../../monitor/models";
import type { ReleaseDiscoveryInput, ReleaseJourney } from "../release-discovery/release-journey";

interface JsonLdOffer {
  availability?: unknown;
  price?: unknown;
  priceCurrency?: unknown;
}

interface JsonLdProduct {
  "@type"?: unknown;
  name?: unknown;
  url?: unknown;
  sku?: unknown;
  mpn?: unknown;
  offers?: JsonLdOffer | JsonLdOffer[];
}

function asProducts(value: unknown, result: JsonLdProduct[] = []): JsonLdProduct[] {
  if (Array.isArray(value)) {
    for (const item of value) asProducts(item, result);
    return result;
  }
  if (!value || typeof value !== "object") return result;
  const record = value as Record<string, unknown>;
  const type = record["@type"];
  if (type === "Product" || (Array.isArray(type) && type.includes("Product"))) result.push(record as JsonLdProduct);
  for (const child of Object.values(record)) {
    if (child && typeof child === "object") asProducts(child, result);
  }
  return result;
}

function firstOffer(product: JsonLdProduct): JsonLdOffer | undefined {
  return Array.isArray(product.offers) ? product.offers[0] : product.offers;
}

function absoluteUrl(value: unknown, baseUrl: string): string | undefined {
  const text = String(value ?? "").trim();
  if (!text) return undefined;
  try { return new URL(text, baseUrl).toString(); }
  catch { return undefined; }
}

function numberValue(value: unknown): number | undefined {
  const parsed = Number(String(value ?? "").replace(",", "."));
  return Number.isFinite(parsed) ? parsed : undefined;
}

function inStock(availability: unknown): boolean {
  return /(?:^|\/)(?:instock|limitedavailability)$/i.test(String(availability ?? "").trim());
}

const FINAL_PURCHASE_TEXT = /(?:zahlungspflichtig\s+bestellen|jetzt\s+(?:kaufen|bezahlen)|bestellung\s+(?:aufgeben|abschließen)|place\s+order|pay\s+now|complete\s+(?:order|purchase)|submit\s+order)/i;
const SAFE_CONTINUE_TEXT = /(?:^|\s)(?:weiter|fortfahren|weiter\s+zur\s+(?:lieferung|versand|zahlung|bezahlung|übersicht)|zur\s+übersicht|continue|continue\s+to\s+(?:shipping|delivery|payment|review)|review\s+order|save\s+and\s+continue)(?:\s|$)/i;

export class PokemonCenterReleaseJourney implements ReleaseJourney {
  private readonly matcher = new ProductMatcher();

  supports(shop: CommerceShop): boolean {
    try {
      const hostname = new URL(shop.baseUrl).hostname;
      return /(^|\.)pokemoncenter\.(com|de)$/i.test(hostname) ||
             hostname === 'localhost' ||
             hostname === '127.0.0.1';
    } catch {
      return false;
    }
  }

  async discover(page: Page, shop: CommerceShop, input: ReleaseDiscoveryInput): Promise<ProductObservation | undefined> {
    const categoryUrl = new URL("/de-de/category/new-releases", shop.baseUrl).toString();
    await page.goto(categoryUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });
    const scripts = await page.locator('script[type="application/ld+json"]').allTextContents();
    const products: JsonLdProduct[] = [];
    for (const script of scripts) {
      try { asProducts(JSON.parse(script) as unknown, products); }
      catch {}
    }

    const criteria = {
      searchTerm: [input.productName, ...input.keywords].filter(Boolean).join(" "),
      requireAvailable: true,
      minimumScore: 0.72
    };
    const ranked: Array<{ observation: ProductObservation; score: number }> = [];
    for (const item of products) {
      const offer = firstOffer(item);
      const observation: ProductObservation = {
        shopId: shop.id,
        platform: shop.platform,
        externalId: String(item.mpn ?? item.sku ?? "").trim() || undefined,
        sku: String(item.sku ?? item.mpn ?? "").trim() || undefined,
        title: String(item.name ?? "").trim(),
        url: absoluteUrl(item.url, shop.baseUrl),
        available: inStock(offer?.availability),
        price: numberValue(offer?.price) !== undefined
          ? { amount: numberValue(offer?.price)!, currency: String(offer?.priceCurrency ?? "").trim() || undefined }
          : undefined,
        observedAt: new Date(),
        attributes: { source: "pokemon-center-jsonld" }
      };
      if (!observation.title || !observation.url) continue;
      const match = this.matcher.match(observation, criteria);
      if (match.matched) ranked.push({ observation, score: match.score });
    }
    ranked.sort((a, b) => b.score - a.score);

    for (const candidate of ranked) {
      // Human path: scroll the category grid down to the matching product card,
      // then click it. Only fall back to a direct navigation when the card
      // cannot be located on the rendered page.
      const anchor = await this.findProductAnchor(page, candidate.observation);
      if (anchor) {
        const href = await anchor.evaluate((element: Element) => element.getAttribute("href") || "").catch(() => "");
        process.stderr.write(`[JOURNEY] discover click-card href=${href} want=${candidate.observation.url ?? ""}\n`);
        await new GhostCursorUiInteractionHelper(page).click(anchor);
        process.stderr.write(`[JOURNEY] discover clicked-card url=${page.url()}\n`);
      } else {
        await page.goto(candidate.observation.url!, { waitUntil: "domcontentloaded", timeout: 30_000 });
        process.stderr.write(`[JOURNEY] discover direct-nav url=${page.url()}\n`);
      }
      // A click-triggered navigation commits a moment after mouseUp, so a
      // single immediate check can still see the category page. Poll for the
      // product's add-to-cart control before giving up, otherwise the caller
      // re-navigates to the category and visibly scrolls back to the top.
      if (await this.waitForAddToCart(page, 12_000)) return candidate.observation;
    }
    return undefined;
  }

  private async waitForAddToCart(page: Page, timeoutMs: number): Promise<boolean> {
    const add = page.locator("button").filter({ hasText: "In den Einkaufswagen" }).first();
    const deadline = Date.now() + timeoutMs;
    do {
      const visible = await add.isVisible().catch(() => false);
      if (visible && await add.isEnabled().catch(() => false)) return true;
      await page.waitForTimeout(400).catch(() => undefined);
    } while (Date.now() < deadline);
    return false;
  }

  private async findProductAnchor(page: Page, product: ProductObservation): Promise<Locator | undefined> {
    const targetPath = (() => {
      try { return new URL(product.url ?? "").pathname; } catch { return ""; }
    })();
    const title = product.title.trim().toLowerCase();
    if (!targetPath && !title) return undefined;
    const anchors = page.locator("a[href]");
    // One evaluateAll instead of one RPC per anchor: a large category grid has
    // hundreds of links and per-anchor round-trips dominated the runtime.
    const matchIndex = await anchors.evaluateAll((elements, args) => {
      const targetPath = String(args.path || "");
      const title = String(args.title || "");
      for (let index = 0; index < elements.length; index++) {
        const href = elements[index].getAttribute("href") || "";
        let path = "";
        try { path = new URL(href, location.href).pathname; } catch { path = ""; }
        const text = (elements[index].textContent || "").toLowerCase();
        if ((targetPath && path === targetPath) || (title && text.includes(title))) return index;
      }
      return -1;
    }, { path: targetPath, title }).catch(() => -1);
    return matchIndex >= 0 ? anchors.nth(matchIndex) : undefined;
  }



  private async waitForCart(page: Page, timeoutMs: number): Promise<boolean> {
    const deadline = Date.now() + timeoutMs;
    do {
      const marker = page.locator("#guest-checkout, [data-ge-checkout-button]").first();
      if (await marker.isVisible().catch(() => false)) return true;
      await page.waitForTimeout(250).catch(() => undefined);
    } while (Date.now() < deadline);
    return false;
  }

  async addToCart(page: Page, shop: CommerceShop, _product: ProductObservation): Promise<void> {
    const add = page.locator("button").filter({ hasText: "In den Einkaufswagen" }).first();
    if (!(await add.isVisible().catch(() => false)) || !(await add.isEnabled().catch(() => false))) {
      throw new Error("Pokémon-Center-Produkt ist nicht mehr in den Einkaufswagen legbar.");
    }
    await new GhostCursorUiInteractionHelper(page).click(add);
    // Follow the shop's own cart transition; only fall back to the known cart
    // URL when the click produced no navigation at all.
    if (await this.waitForCart(page, 10_000)) {
      process.stderr.write(`[JOURNEY] addToCart natural url=${page.url()}\n`);
    } else {
      const cartUrl = new URL("/de-de/cart", shop.baseUrl).toString();
      process.stderr.write(`[JOURNEY] addToCart cart-fallback url=${page.url()}\n`);
      await page.goto(cartUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });
    }
    const guest = page.locator("#guest-checkout").first();
    if (!(await guest.isVisible().catch(() => false))) {
      throw new Error("Pokémon-Center-Warenkorb enthält keinen sichtbaren Gast-Checkout.");
    }
  }

  private async waitForCheckout(page: Page, timeoutMs: number): Promise<boolean> {
    const deadline = Date.now() + timeoutMs;
    do {
      const title = await page.title().catch(() => "");
      if (/(checkout|global-e|international)/i.test(title)) return true;
      const marker = page.locator("#geCheckoutFrm, #BillingCountryID");
      if (await marker.count().catch(() => 0) > 0) return true;
      await page.waitForTimeout(250).catch(() => undefined);
    } while (Date.now() < deadline);
    return false;
  }

  async openCheckout(page: Page, shop: CommerceShop): Promise<void> {
    const guestById = page.locator("#guest-checkout").first();
    const guest = await guestById.isVisible().catch(() => false)
      ? guestById
      : page.locator("button").filter({ hasText: "Als Gast zur Kasse" }).first();
    if (!(await guest.isVisible().catch(() => false)) || !(await guest.isEnabled().catch(() => false))) {
      throw new Error("Pokémon-Center-Gast-Checkout ist nicht verfügbar.");
    }
    await new GhostCursorUiInteractionHelper(page).click(guest);
    // Follow the shop's own (session-bound) checkout redirect; only fall back
    // to the known checkout URL when the click produced no navigation.
    if (await this.waitForCheckout(page, 10_000)) {
      process.stderr.write(`[JOURNEY] openCheckout natural url=${page.url()}\n`);
    } else {
      const checkoutUrl = new URL("/de-de/intl-checkout", shop.baseUrl).toString();
      process.stderr.write(`[JOURNEY] openCheckout checkout-fallback url=${page.url()}\n`);
      await page.goto(checkoutUrl, { waitUntil: "domcontentloaded", timeout: 30_000 }).catch(() => undefined);
    }
    await page.waitForLoadState("domcontentloaded", { timeout: 8_000 }).catch(() => undefined);
    const title = await page.title().catch(() => "");
    const current = page.url();
    process.stderr.write(`[JOURNEY] openCheckout url=${current} title=${title}\n`);
    if (!/(checkout|global-e|international)/i.test(`${title} ${current}`)) {
      throw new Error("Pokémon-Center-Checkout wurde nach Gast-Checkout nicht bestätigt.");
    }
  }

  async isReadyForFinalSubmit(page: Page, _shop: CommerceShop): Promise<boolean> {
    return Boolean(await this.findButton(page, FINAL_PURCHASE_TEXT));
  }

  async isOrderConfirmed(page: Page, _shop: CommerceShop): Promise<boolean> {
    return (await observeCheckoutOutcome(page)).confirmed;
  }

  async advanceCheckout(page: Page, _shop: CommerceShop): Promise<boolean> {
    const candidate = await this.findButton(page, SAFE_CONTINUE_TEXT, FINAL_PURCHASE_TEXT);
    if (!candidate) return false;
    const label = await candidate.evaluate(element => [
      element.textContent || "",
      element.getAttribute("value") || "",
      element.getAttribute("aria-label") || ""
    ].join(" ").replace(/\s+/g, " ").trim()).catch(() => "");
    process.stderr.write(`[JOURNEY] advanceCheckout click="${label}"\n`);
    await new GhostCursorUiInteractionHelper(page).click(candidate);
    await page.waitForLoadState("domcontentloaded", { timeout: 12_000 }).catch(() => undefined);
    await page.waitForTimeout(350).catch(() => undefined);
    return true;
  }

  /** Returns only whether the guarded irreversible click was dispatched. */
  async submitOrder(page: Page, _shop: CommerceShop, allowFinalPurchase: () => boolean): Promise<boolean> {
    const candidate = await this.findButton(page, FINAL_PURCHASE_TEXT);
    if (!candidate) return false;

    // Hard backend-side guard immediately before the irreversible submit click.
    if (!allowFinalPurchase()) return false;
    await new GhostCursorUiInteractionHelper(page).click(candidate);
    await page.waitForLoadState("domcontentloaded", { timeout: 20_000 }).catch(() => undefined);
    return true;
  }

  private async findButton(page: Page, include: RegExp, exclude?: RegExp): Promise<Locator | undefined> {
    const candidates = page.locator('button, input[type="submit"], [role="button"]');
    const count = Math.min(await candidates.count().catch(() => 0), 120);
    for (let index = 0; index < count; index++) {
      const candidate = candidates.nth(index);
      if (!await candidate.isVisible().catch(() => false) || !await candidate.isEnabled().catch(() => false)) continue;
      const text = await candidate.evaluate(element => [
        element.textContent || "",
        element.getAttribute("value") || "",
        element.getAttribute("aria-label") || "",
        element.getAttribute("data-test") || "",
        element.getAttribute("data-testid") || ""
      ].join(" ").replace(/\s+/g, " ").trim()).catch(() => "");
      if (!include.test(text)) continue;
      if (exclude?.test(text)) continue;
      return candidate;
    }
    return undefined;
  }
}
