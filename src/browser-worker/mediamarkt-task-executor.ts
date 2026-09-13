import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import type { ITaskExecutor } from "../interfaces";
import type { Task } from "../models";
import type { CommerceShop } from "../commerce/platforms";
import type { AresProfile } from "../profiles/models";
import type { CheckoutPaymentSession, PaymentPreparationResult } from "../payments/models";
import type { BrowserWorker } from "./browser-worker";
import type { BrowserContextHandle, Locator, Page } from "./types";
import { CheckoutPaymentPreparer } from "./checkout-payment-preparer";
import { evaluatePaymentReadiness, type PaymentReadinessReason } from "./payment-readiness";
import { confirmFinalSubmitWithRetries } from "./final-submit-recovery";
import { SemanticCheckoutPreparer, type SemanticCheckoutPreparationResult } from "./semantic-checkout-preparer";
import { normalizeDiscoveryKeywords } from "../monitor/early-gate";
import type { ProductObservation } from "../monitor/models";
import type { ReleaseJourney } from "../commerce/release-discovery/release-journey";
import { EXCLUDED_MARKETING_SELECTOR } from "../commerce/mediamarkt/release-journey";
import { runAccountRegistration } from "./account-registration-runner";
import { LiveChallengeHandler } from "../challenges/live-challenge-handler";
import { GhostCursorUiInteractionHelper } from "./ui-interaction-helper";

// Best-effort consent handling. These are ordinary element clicks through the
// native locator path; no page script is injected or evaluated to write.
const CONSENT_TEXTS: RegExp[] = [
  /\balle(?:\s+cookies)?\s+akzeptieren\b/i,
  /\bcookies akzeptieren\b/i,
  /\bakzeptieren und weiter\b/i,
  /\bzustimmen\b/i,
  /\beinverstanden\b/i,
  /\baccept all(?:\s+cookies)?\b/i,
  // "Only necessary" also unblocks input and is the privacy-safe choice.
  /\bnur notwendige\b/i,
  /\bnur erforderliche\b/i,
  /\bnur essenzielle\b/i,
  /\bnotwendige akzeptieren\b/i,
  /\bonly necessary\b/i
];

interface ActiveDirectSession {
  task: Task;
  controller: AbortController;
}

interface DiscoveryInput {
  productName: string;
  keywords: string[];
  /** When set (auto-checkout child), go straight to this product URL. */
  productUrl?: string;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

/**
 * MediaMarkt direct lane: one browser, no queue, no monitor spawn.
 *
 * The task is the product-triggered child of the module's monitor parent (or a
 * harness task with the same data shape). It discovers the product through the
 * shop's own search, adds it to the cart, advances the checkout wizard to the
 * payment step, prepares profile and payment data, and then waits behind the
 * global final-purchase guard.
 */
export class MediaMarktTaskExecutor implements ITaskExecutor {
  private readonly active = new Map<string, ActiveDirectSession>();
  private readonly checkoutPreparer = new SemanticCheckoutPreparer();
  private readonly paymentPreparer = new CheckoutPaymentPreparer({
    // Top-to-bottom like a person on the MediaMarkt payment page.
    cardFillOrder: ["cardNumber", "expiry", "securityCode", "holderName"]
  });
  private readonly challengeHandler = new LiveChallengeHandler();
  private allowFinalPurchase = false;

  constructor(
    private readonly getShop: (shopId: string) => CommerceShop | undefined,
    private readonly getProfile: (profileId: string) => AresProfile | undefined,
    private readonly resolveJourney: (shop: CommerceShop) => ReleaseJourney | undefined,
    private readonly browserWorker: BrowserWorker,
    private readonly onTaskUpdate: (task: Task) => void = () => undefined
  ) {}

  async execute(task: Task, paymentSession?: CheckoutPaymentSession, existingHandle?: BrowserContextHandle): Promise<boolean> {
    const shopId = task.config.shopId;
    const profileId = String(task.config.data?.["profileId"] ?? "").trim();
    const discovery = this.resolveDiscoveryInput(task);
    if (!shopId || !profileId || !discovery.productName) {
      task.lastError = "MediaMarkt-Direct benötigt shopId, profileId und einen Produktnamen.";
      return false;
    }
    const shop = this.getShop(shopId);
    const profile = this.getProfile(profileId);
    if (!shop || !profile) {
      task.lastError = !shop ? `Shop ${shopId} ist nicht registriert.` : `Profil ${profileId} ist nicht registriert.`;
      return false;
    }
    const journey = this.resolveJourney(shop);
    if (!journey?.supports(shop)) {
      task.lastError = `Für ${shop.name} ist keine MediaMarkt-Release-Journey registriert.`;
      return false;
    }
    if (this.active.has(task.id)) {
      task.lastError = `MediaMarkt-Browser-Task ${task.id} läuft bereits.`;
      return false;
    }

    const session: ActiveDirectSession = { task, controller: new AbortController() };
    this.active.set(task.id, session);

    const profileRoot = process.env["ARES_BROWSER_PROFILE_ROOT"]?.trim() || path.join(os.tmpdir(), "ares-browser-profiles");
    fs.mkdirSync(profileRoot, { recursive: true });
    const userDataDir = path.join(profileRoot, task.id.replace(/[^a-zA-Z0-9_-]/g, "_"));
    fs.mkdirSync(userDataDir, { recursive: true });
    const proxy = profile.proxy?.host && profile.proxy.port ? {
      protocol: profile.proxy.protocol || "http" as const,
      host: profile.proxy.host,
      port: profile.proxy.port,
      username: profile.proxy.username || undefined,
      password: profile.proxy.password || undefined
    } : undefined;

    try {
      const handle = existingHandle ?? await this.browserWorker.createContext({
        taskId: task.id,
        targetId: shop.platform,
        userDataDir,
        headless: profile.browser?.headless ?? Boolean((task.config.data?.["browserConfig"] as Record<string, unknown> | undefined)?.["headless"]),
        proxy,
        userAgent: profile.browser?.userAgent || undefined,
        viewport: null,
        navigationTimeoutMs: 30_000,
        actionTimeoutMs: 15_000
      });
      const page = handle.page;
      await (page as unknown as { reinstallStealthSpoof?: () => Promise<boolean> })
        .reinstallStealthSpoof?.()
        .catch(() => false);
      // MediaMarkt-only scroll profile: slower, smaller wheel steps so the
      // movement stays followable. Other modules keep the default motion.
      await (page as unknown as { setScrollProfile?: (profile?: Record<string, number>) => Promise<void> })
        .setScrollProfile?.({
          speedMin: 140, speedMax: 320,
          stepMin: 260, stepMax: 460,
          chunkMin: 40, chunkMax: 95,
          pauseMin: 0.07, pauseMax: 0.18,
          settleMin: 0.22, settleMax: 0.42
        })
        .catch(() => undefined);
      task.config.data = {
        ...(task.config.data ?? {}),
        browserSession: { type: "seleniumbase-cdp", isolatedPerTask: true, userDataDir: handle.userDataDir },
        browserEnvironment: handle.environmentAudit
      };

      // Isolated account-registration lane: no discovery, no cart, no checkout.
      // Generic runner: a no-op for every task without accountRegistration data.
      const registrationResult = await runAccountRegistration({
        task,
        page,
        shop,
        journey,
        signal: session.controller.signal
      });
      if (registrationResult !== undefined) {
        this.emit(task);
        return registrationResult;
      }

      this.markStage(task, "discovery");
      process.stderr.write(`[JOURNEY] mediamarkt-direct start product="${discovery.productName}" keywords=${discovery.keywords.length}\n`);

      const discoveryDeadline = Date.now() + this.discoveryMaxMs(task);
      let product: ProductObservation | undefined;
      if (discovery.productUrl) {
        // Auto-checkout child: the monitor already matched the product, so go
        // straight to the PDP instead of searching again.
        process.stderr.write(`[JOURNEY] mediamarkt direct-url ${discovery.productUrl}\n`);
        product = await this.withDebugSync(task, page, () => journey.openProductUrl
          ? journey.openProductUrl(page, shop, discovery.productUrl!, discovery.productName)
          : Promise.resolve(undefined));
        this.syncDebug(task, page);
        this.emit(task);
      } else {
        while (!session.controller.signal.aborted && Date.now() < discoveryDeadline) {
          product = await this.withDebugSync(task, page, () => journey.discover(page, shop, {
            productName: discovery.productName,
            keywords: [...discovery.keywords]
          }));
          this.syncDebug(task, page);
          this.emit(task);
          if (product) break;
          // A challenge can appear on the storefront/search step; solve it before
          // the next discovery attempt instead of retrying into a blocked page.
          await this.handleChallenges(page, task, 15_000);
          await this.delay(this.discoveryIntervalMs(task), session.controller.signal);
        }
      }
      if (session.controller.signal.aborted) return true;
      if (!product) throw new Error("MediaMarkt-Discovery-Zeitfenster ohne passenden Produkt-Treffer beendet.");

      const foundProduct = product;
      task.config.data = {
        ...(task.config.data ?? {}),
        releaseProduct: {
          title: foundProduct.title,
          url: foundProduct.url,
          externalId: foundProduct.externalId,
          sku: foundProduct.sku
        }
      };
      this.markStage(task, "product-found");
      process.stderr.write(`[JOURNEY] mediamarkt product found "${foundProduct.title}" url=${foundProduct.url}\n`);

      await this.delay(300, session.controller.signal);
      await this.dismissConsent(page);
      await this.withDebugSync(task, page, () => journey.addToCart(page, shop, foundProduct));
      this.syncDebug(task, page);
      this.markStage(task, "cart");
      process.stderr.write("[JOURNEY] mediamarkt cart reached\n");

      await this.delay(300, session.controller.signal);
      await this.dismissConsent(page);
      await this.withDebugSync(task, page, () => journey.openCheckout(page, shop));
      await this.handleChallenges(page, task, 20_000);
      await this.delay(300, session.controller.signal);
      this.syncDebug(task, page);
      const initialPaymentReadiness = evaluatePaymentReadiness(paymentSession, undefined);
      this.publishCheckoutPreparation(task, {
        phase: "checkout-opened",
        profileReady: false,
        paymentReady: initialPaymentReadiness.ready,
        paymentReadinessReason: initialPaymentReadiness.reason,
        reviewReady: false
      });

      await this.prepareCheckoutUntilReady(task, session, journey, page, shop, profile, paymentSession);
      if (session.controller.signal.aborted) return true;

      this.markStage(task, "checkout");
      process.stderr.write("[JOURNEY] mediamarkt checkout ready; final purchase guard active\n");
      this.publishFinalPurchaseStatus(task, "blocked");

      while (!session.controller.signal.aborted) {
        if (!this.allowFinalPurchase) {
          await this.delay(250, session.controller.signal);
          continue;
        }

        const submitted = await journey.submitOrder(page, shop, () => this.allowFinalPurchase);
        if (submitted) {
          this.publishFinalPurchaseStatus(task, "submitted");
          const recovery = await confirmFinalSubmitWithRetries(
            () => journey.isOrderConfirmed?.(page, shop) ?? Promise.resolve(false),
            {
              attempts: this.orderConfirmationAttempts(task),
              delayMs: this.orderConfirmationRetryDelayMs(task),
              signal: session.controller.signal
            }
          );
          if (recovery.confirmed) {
            this.publishFinalPurchaseStatus(task, "confirmed");
            return true;
          }

          task.lastError = "Finaler Bestell-Submit wurde ausgelöst, aber kein bestätigter Bestellerfolg erkannt. Nach zwei Recovery-Versuchen wird aus Sicherheitsgründen nicht erneut abgesendet.";
          this.blockRetryAfterAmbiguousSubmit(task, recovery.attempts, recovery.maxAttempts);
          this.publishFinalPurchaseStatus(task, "confirmation-missing");
          this.emit(task);
          return false;
        }

        this.publishFinalPurchaseStatus(task, this.allowFinalPurchase ? "not-ready" : "blocked");
        await this.delay(1_000, session.controller.signal);
      }
      return true;
    } catch (error) {
      if (session.controller.signal.aborted) return true;
      task.lastError = error instanceof Error ? error.message : String(error);
      this.emit(task);
      await this.browserWorker.closeContext(task.id).catch(() => undefined);
      return false;
    } finally {
      this.active.delete(task.id);
    }
  }

  async setFinalPurchaseAllowed(allowed: boolean): Promise<void> {
    this.allowFinalPurchase = allowed === true;
    for (const session of this.active.values()) {
      if (this.flowStage(session.task) === "checkout") {
        this.publishFinalPurchaseStatus(session.task, this.allowFinalPurchase ? "armed" : "blocked");
      }
    }
  }

  async cancelTask(taskId: string): Promise<void> {
    this.active.get(taskId)?.controller.abort();
    this.active.delete(taskId);
    await this.browserWorker.closeContext(taskId);
  }

  async closeAll(): Promise<void> {
    const taskIds = [...this.active.keys()];
    for (const session of this.active.values()) session.controller.abort();
    this.active.clear();
    await Promise.allSettled(taskIds.map(taskId => this.browserWorker.closeContext(taskId)));
  }

  private resolveDiscoveryInput(task: Task): DiscoveryInput {
    const data = task.config.data ?? {};
    const postQueue = asRecord(data["postQueueDiscovery"]);
    const trigger = asRecord(data["triggerSource"]);
    const criteria = asRecord(data["productCriteria"]);
    const productName = String(
      postQueue?.["productName"] ?? trigger?.["productTitle"] ?? criteria?.["searchTerm"] ?? data["searchTerm"] ?? task.config.name ?? ""
    ).trim();
    const keywords = normalizeDiscoveryKeywords(postQueue?.["keywords"] ?? []);
    const rawUrl = String(trigger?.["productUrl"] ?? criteria?.["url"] ?? data["productUrl"] ?? "").trim();
    const productUrl = /^https?:\/\//i.test(rawUrl) ? rawUrl : undefined;
    return { productName, keywords, productUrl };
  }

  // TEMPORARY LIVE DEBUG (remove after live validation): mirror the journey's
  // click/transition ring buffer into the task data so it persists in SQLite.
  private syncDebug(task: Task, page: Page): void {
    const buffer = page["mediamarktDebug"] as Array<Record<string, unknown>> | undefined;
    if (!buffer?.length) return;
    task.config.data = { ...(task.config.data ?? {}), mediamarktDebug: buffer.slice(-120) };
  }

  /** Runs a long journey call while flushing the debug buffer every 2s. */
  private async withDebugSync<T>(task: Task, page: Page, run: () => Promise<T>): Promise<T> {
    const timer = setInterval(() => {
      this.syncDebug(task, page);
      this.flushDebugFile(page);
      this.emit(task);
    }, 2_000);
    try {
      return await run();
    } finally {
      clearInterval(timer);
      this.syncDebug(task, page);
      this.flushDebugFile(page);
      this.emit(task);
    }
  }

  // TEMPORARY LIVE DEBUG: append new ring-buffer entries to a plain file so the
  // live flow is inspectable even when task updates are throttled or the run
  // hangs. Default: %TEMP%\ares-mediamarkt-debug.log
  private flushDebugFile(page: Page): void {
    const buffer = page["mediamarktDebug"] as Array<Record<string, unknown>> | undefined;
    const written = Number(page["mediamarktDebugWritten"] ?? 0);
    if (!buffer || buffer.length <= written) return;
    const target = process.env["ARES_MEDIAMARKT_DEBUG_FILE"]?.trim()
      || path.join(os.tmpdir(), "ares-mediamarkt-debug.log");
    const lines = buffer.slice(written).map(entry => JSON.stringify(entry)).join("\n") + "\n";
    try {
      fs.appendFileSync(target, lines, "utf8");
    } catch {
      // Debug must never break the flow.
    }
    page["mediamarktDebugWritten"] = buffer.length;
  }
  /** Quick CMP dismissal on non-entry pages; one visible accept check only. */
  private async dismissConsent(page: Page): Promise<void> {
    const selectors = [
      '[data-test="pwa-consent-layer-accept-all"]',
      '#onetrust-accept-btn-handler',
      '[data-test="pwa-consent-layer-deny-all"]',
      '#onetrust-reject-all-handler'
    ];
    for (const selector of selectors) {
      const candidate = page.locator(selector).first();
      if (!await candidate.isVisible({ timeout: 150 }).catch(() => false)) continue;
      if (await this.isMarketingElement(candidate)) continue;
      await candidate.click({ timeout: 3_000 }).catch(() => undefined);
      await page.waitForTimeout(250).catch(() => undefined);
      return;
    }
  }

  /** Marketing surfaces (newsletter/teaser/carousel/ad) are never clicked. */
  private async isMarketingElement(candidate: Locator): Promise<boolean> {
    return candidate.evaluate((element: Element, excluded: string) => Boolean(element.closest(excluded)), EXCLUDED_MARKETING_SELECTOR)
      .catch(() => false);
  }

  /** Cheap grid/captcha poll through the existing vision runtime. */
  private async solveChallenge(page: Page): Promise<void> {
    const solver = (page as unknown as { solveCaptcha?: () => Promise<boolean> }).solveCaptcha;
    if (typeof solver !== "function") return;
    await solver.call(page).catch(() => false);
  }

  /**
   * Detection first: only a visibly rendered challenge engages the solvers.
   * Page-source leftovers (e.g. DataDome script references) must not trigger
   * the heavy vision path.
   */
  private async handleChallenges(page: Page, task: Task, timeoutMs: number): Promise<void> {
    if (!await this.hasVisibleChallenge(page)) return;
    process.stderr.write("[JOURNEY] mediamarkt challenge detected\n");
    await this.solveChallenge(page);
    const result = await this.challengeHandler.handleLiveChallenge(page, {
      timeoutMs,
      onStatusChange: status => {
        task.config.data = { ...(task.config.data ?? {}), liveChallengeStatus: status };
        this.emit(task);
      }
    }).catch(() => undefined);
    if (result?.type) {
      task.config.data = { ...(task.config.data ?? {}), liveChallengeType: result.type };
      this.emit(task);
    }
    if (result?.handled && !result.resolved) {
      process.stderr.write(`[JOURNEY] mediamarkt challenge unresolved type=${result.type ?? "unknown"}\n`);
    }
  }

  private async hasVisibleChallenge(page: Page): Promise<boolean> {
    const selectors = [
      'iframe[src*="recaptcha"]',
      'iframe[src*="hcaptcha"]',
      'iframe[src*="turnstile"]',
      'iframe[src*="captcha-delivery"]',
      '.captcha-grid',
      '#captcha__frame',
      '[data-dd-captcha-container]',
      '#challenge-running'
    ];
    for (const selector of selectors) {
      if (await page.locator(selector).first().isVisible({ timeout: 200 }).catch(() => false)) return true;
    }
    return false;
  }

  private async prepareCheckoutUntilReady(
    task: Task,
    session: ActiveDirectSession,
    journey: ReleaseJourney,
    page: Page,
    shop: CommerceShop,
    profile: AresProfile,
    paymentSession?: CheckoutPaymentSession
  ): Promise<void> {
    await this.waitForCheckoutFields(task, session, page);
    const deadline = Date.now() + this.checkoutPreparationMaxMs(task);
    let profileReady = false;
    let lastProfile: SemanticCheckoutPreparationResult | undefined;
    let lastPayment: PaymentPreparationResult | undefined;
    let lastPaymentReason: PaymentReadinessReason = paymentSession ? "missing-preparation" : "missing-session";
    let iterations = 0;
    let addressSearchResolved = false;
    let addressSearchAttempts = 0;
    const guestProfile: AresProfile = {
      ...profile,
      // MediaMarkt marks telephone as optional. Leaving it empty avoids its
      // numeric type=tel controls being revisited by generic field heuristics.
      contact: { ...profile.contact, phone: "" }
    };

    while (!session.controller.signal.aborted && Date.now() < deadline) {
      iterations += 1;
      this.syncDebug(task, page);
      // Detection-only cycle: the challenge solvers engage only when a
      // challenge is actually recognized on the current document.
      if (iterations % 4 === 0) await this.handleChallenges(page, task, 8_000);

      // One pass is enough on the MediaMarkt payment step: it has no profile
      // fields, so the semantic preparer only needs to observe that nothing is
      // left to fill. Repeating it burned ~8s per extra attempt.
      // Fill the plain profile fields (email, first/last name) first: MediaMarkt
      // renders them above the address block. Only then use the shop's own
      // "Adresse suchen" autocomplete for the address.
      if (!profileReady) {
        lastProfile = await this.checkoutPreparer.prepare(page, guestProfile, { maxAttempts: 1 }).catch(() => undefined);
      }
      if (!addressSearchResolved && addressSearchAttempts < 3) {
        addressSearchAttempts += 1;
        addressSearchResolved = await this.fillAddressSearch(page, profile).catch(() => false);
        if (!addressSearchResolved && addressSearchAttempts < 3) {
          await this.delay(400, session.controller.signal);
          continue;
        }
        if (!addressSearchResolved) {
          throw new Error("MediaMarkt: 'Adresse suchen' wurde nicht erkannt; Einzeladressfelder werden nicht als Fallback befüllt.");
        }
      }
      // A checkout step with no observable profile fields has nothing left to
      // complete (MediaMarkt keeps the address on file for guest/returning
      // sessions). Only observed but unfilled required fields block readiness.
      if (!profileReady && lastProfile && (lastProfile.requiredTargetsSatisfied || lastProfile.requiredTargetCount === 0)) profileReady = true;

      lastPayment = await this.paymentPreparer.prepare(page, paymentSession).catch(error => ({
        detectedMethods: [],
        selectedMethod: paymentSession?.method,
        filledFields: [],
        missingFields: [],
        requiresUserAction: true,
        note: `Zahlungsprüfung fehlgeschlagen: ${error instanceof Error ? error.message : String(error)}`
      }));

      const paymentReadiness = evaluatePaymentReadiness(paymentSession, lastPayment);
      lastPaymentReason = paymentReadiness.reason;
      const finalControlReady = await journey.isReadyForFinalSubmit(page, shop).catch(() => false);
      const paymentSatisfied = paymentReadiness.ready || !paymentSession;
      const preparationReady = profileReady && paymentSatisfied;
      const reviewReady = preparationReady && finalControlReady;
      this.publishCheckoutPreparation(task, {
        phase: reviewReady ? "purchase-ready" : "preparing",
        profileReady,
        paymentReady: paymentReadiness.ready,
        paymentReadinessReason: paymentReadiness.reason,
        reviewReady,
        profile: lastProfile,
        payment: lastPayment
      });
      if (preparationReady) return;

      const advanced = await journey.advanceCheckout(page, shop).catch(() => false);
      if (!advanced) await this.delay(700, session.controller.signal);
    }

    if (session.controller.signal.aborted) return;
    const reason = !profileReady
      ? "Checkout-Adresse/Profil wurde nicht vollständig bestätigt."
      : paymentSession && lastPaymentReason !== "ready"
        ? `Checkout-Zahlung ist nicht kaufbereit (${lastPaymentReason}).`
        : "Finaler kaufbereiter Review-/Submit-Zustand wurde nicht erreicht.";
    throw new Error(reason);
  }

  /** Uses MediaMarkt's own address autocomplete before individual field fill. */
  private async fillAddressSearch(page: Page, profile: AresProfile): Promise<boolean> {
    const address = profile.address;
    const street = String(address.address1 || [address.street, address.houseNumber].filter(Boolean).join(" ")).trim();
    const query = [street, address.postalCode, address.city].filter(Boolean).join(", ");
    if (!query) return false;

    // Match the Loqate autocomplete on its own. A combined selector returns
    // matches in DOM order, and `main input[name*="address" i]` matches the
    // address-user-type radios above the field, so `.first()` would pick an
    // invisible radio and the address step was reported as "not recognized".
    const loqate = page.locator('input[id*="loqate" i], input[name*="loqate" i]').first();
    let field: Locator | undefined;
    if (await loqate.isVisible({ timeout: 1_000 }).catch(() => false)) {
      field = loqate;
    } else {
      const generic = page.locator([
        'main input[role="combobox"]',
        'main [data-test*="address" i] input[role="combobox"]',
        'main [data-test*="address" i] input',
        'main input[id*="address" i]',
        'input[placeholder*="adresse suchen" i]',
        'input[aria-label*="adresse suchen" i]',
        'input[name*="addressSearch" i]',
        'input[data-test*="address-search" i]'
      ].join(", ")).first();
      if (await generic.isVisible({ timeout: 1_000 }).catch(() => false)) field = generic;
    }
    if (!field) {
      // TEMPORARY LIVE DEBUG (remove after live validation): dump the visible
      // form controls so the real address-search selector can be derived.
      const candidates = await page.evaluate(() => Array.from(
        document.querySelectorAll('input, textarea, select, [role="combobox"]')
      ).filter(element => {
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      }).slice(0, 40).map(element => ({
        tag: element.tagName.toLowerCase(),
        type: element.getAttribute("type") || "",
        id: (element as HTMLElement).id || "",
        name: element.getAttribute("name") || "",
        placeholder: element.getAttribute("placeholder") || "",
        aria: element.getAttribute("aria-label") || "",
        test: element.getAttribute("data-test") || "",
        role: element.getAttribute("role") || ""
      }))).catch(() => []);
      process.stderr.write(`[JOURNEY] mediamarkt address-field-missing ${JSON.stringify(candidates)}\n`);
      return false;
    }

    await new GhostCursorUiInteractionHelper(page).type(field, query, {
      attempts: 1,
      seed: `mediamarkt-address:${query}`,
      interKeyDelayMinMs: 45,
      interKeyDelayMaxMs: 90,
      typoProbability: 0
    });
    await page.waitForTimeout(500).catch(() => undefined);
    // Click the best autocomplete match. Typing alone does not commit the
    // address in MediaMarkt's Loqate widget; the selected suggestion is what
    // fills the individual address fields.
    const suggestion = page.locator([
      '[role="listbox"] [role="option"]',
      'ul[role="listbox"] li',
      '[class*="loqate" i] [role="option"]',
      '[class*="loqate" i] li',
      '[class*="autocomplete" i] li',
      '[class*="suggestion" i] li',
      '[data-test*="address" i] li'
    ].join(", ")).first();
    const appeared = await suggestion.waitFor({ state: "visible", timeout: 6_000 })
      .then(() => true)
      .catch(() => false);
    if (appeared) {
      await suggestion.click({ timeout: 6_000 }).catch(() => undefined);
    } else {
      // Fallback: keyboard selection when the list is already focused.
      await field.press("ArrowDown", { timeout: 3_000, focus: false }).catch(() => undefined);
      await field.press("Enter", { timeout: 3_000, focus: false }).catch(() => undefined);
    }
    await page.waitForTimeout(600).catch(() => undefined);
    return true;
  }

  private publishCheckoutPreparation(task: Task, input: {
    phase: "checkout-opened" | "preparing" | "purchase-ready";
    profileReady: boolean;
    paymentReady: boolean;
    paymentReadinessReason: PaymentReadinessReason;
    reviewReady: boolean;
    profile?: SemanticCheckoutPreparationResult;
    payment?: PaymentPreparationResult;
  }): void {
    task.config.data = {
      ...(task.config.data ?? {}),
      checkoutPreparation: {
        phase: input.phase,
        profileReady: input.profileReady,
        paymentReady: input.paymentReady,
        paymentReadinessReason: input.paymentReadinessReason,
        reviewReady: input.reviewReady,
        ...(input.profile ? {
          profile: {
            billingMode: input.profile.billingMode,
            requiredTargetsSatisfied: input.profile.requiredTargetsSatisfied,
            requiredTargetCount: input.profile.requiredTargetCount,
            filled: input.profile.filled,
            missing: input.profile.missing,
            writeCounts: input.profile.writeCounts
          }
        } : {}),
        ...(input.payment ? { payment: input.payment } : {}),
        updatedAt: new Date().toISOString()
      },
      ...(input.payment ? { paymentPreparation: input.payment } : {})
    };
    this.emit(task);
  }

  private publishFinalPurchaseStatus(
    task: Task,
    status: "blocked" | "armed" | "not-ready" | "submitted" | "confirmed" | "confirmation-missing"
  ): void {
    const now = new Date().toISOString();
    const previous = task.config.data?.["finalPurchaseRuntime"] as Record<string, unknown> | undefined;
    task.config.data = {
      ...(task.config.data ?? {}),
      finalPurchaseRuntime: {
        ...(previous ?? {}),
        allowFinalPurchase: this.allowFinalPurchase,
        status,
        updatedAt: now,
        ...((status === "submitted" || status === "confirmed" || status === "confirmation-missing") && !previous?.["submittedAt"] ? { submittedAt: now } : {}),
        ...(status === "confirmed" ? { confirmedAt: now } : {})
      }
    };
    this.emit(task);
  }

  private blockRetryAfterAmbiguousSubmit(task: Task, attempts: number, maxAttempts: number): void {
    task.config.data = {
      ...(task.config.data ?? {}),
      retryPolicy: {
        blocked: true,
        reason: "ambiguous-final-submit",
        attempts,
        maxAttempts,
        updatedAt: new Date().toISOString()
      }
    };
  }

  private markStage(task: Task, stage: "discovery" | "product-found" | "cart" | "checkout"): void {
    task.config.data = {
      ...(task.config.data ?? {}),
      mediamarktFlow: {
        ...(asRecord(task.config.data?.["mediamarktFlow"]) ?? {}),
        stage,
        updatedAt: new Date().toISOString()
      }
    };
    this.emit(task);
  }

  private flowStage(task: Task): string {
    const flow = asRecord(task.config.data?.["mediamarktFlow"]);
    return String(flow?.["stage"] ?? "");
  }

  private async waitForCheckoutFields(task: Task, session: ActiveDirectSession, page: Page): Promise<void> {
    const deadline = Date.now() + this.checkoutFieldLoadMaxMs(task);
    while (!session.controller.signal.aborted && Date.now() < deadline) {
      const count = await this.checkoutPreparer.observeCount(page).catch(() => 0);
      if (count >= 3 && await this.checkoutPreparer.observeReady(page).catch(() => false)) return;
      await this.delay(500, session.controller.signal);
    }
  }

  private emit(task: Task): void {
    this.onTaskUpdate(task);
  }

  private discoveryMaxMs(task: Task): number {
    const raw = Number(task.config.data?.["discoveryMaxMs"] ?? 10 * 60_000);
    return Number.isFinite(raw) ? Math.min(30 * 60_000, Math.max(30_000, raw)) : 10 * 60_000;
  }

  private discoveryIntervalMs(task: Task): number {
    const raw = Number(task.config.data?.["discoveryIntervalMs"] ?? 3_000);
    return Number.isFinite(raw) ? Math.min(30_000, Math.max(1_000, raw)) : 3_000;
  }

  private checkoutFieldLoadMaxMs(task: Task): number {
    const raw = Number(task.config.data?.["checkoutFieldLoadMaxMs"] ?? 3 * 60_000);
    return Number.isFinite(raw) ? Math.min(20 * 60_000, Math.max(15_000, raw)) : 3 * 60_000;
  }

  private checkoutPreparationMaxMs(task: Task): number {
    const raw = Number(task.config.data?.["checkoutPreparationMaxMs"] ?? 8 * 60_000);
    return Number.isFinite(raw) ? Math.min(30 * 60_000, Math.max(30_000, raw)) : 8 * 60_000;
  }

  private orderConfirmationAttempts(task: Task): number {
    const raw = Number(task.config.data?.["orderConfirmationAttempts"] ?? 2);
    return Number.isFinite(raw) ? Math.min(5, Math.max(2, Math.floor(raw))) : 2;
  }

  private orderConfirmationRetryDelayMs(task: Task): number {
    const raw = Number(task.config.data?.["orderConfirmationRetryDelayMs"] ?? 750);
    return Number.isFinite(raw) ? Math.min(5_000, Math.max(0, Math.floor(raw))) : 750;
  }

  private delay(ms: number, signal: AbortSignal): Promise<void> {
    if (signal.aborted) return Promise.resolve();
    return new Promise(resolve => {
      const timer = setTimeout(done, ms);
      function done(): void {
        clearTimeout(timer);
        signal.removeEventListener("abort", done);
        resolve();
      }
      signal.addEventListener("abort", done, { once: true });
    });
  }
}
