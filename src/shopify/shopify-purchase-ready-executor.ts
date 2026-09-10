import type { ITaskExecutor } from "../interfaces";
import type { Task } from "../models";
import type { AresProfile } from "../profiles/models";
import type { CheckoutPaymentSession, PaymentPreparationResult } from "../payments/models";
import type { Page } from "../browser-worker/types";
import {
  SemanticCheckoutPreparer,
  type SemanticCheckoutPreparationResult
} from "../browser-worker/semantic-checkout-preparer";
import { CheckoutPaymentPreparer } from "../browser-worker/checkout-payment-preparer";
import { evaluatePaymentReadiness, type PaymentReadinessReason } from "../browser-worker/payment-readiness";
import { confirmFinalSubmitWithRetries } from "../browser-worker/final-submit-recovery";
import { ShopifyCheckoutJourney } from "./checkout-journey";

interface ShopifyCheckoutBaseExecutor {
  execute(task: Task): Promise<boolean>;
  closeTask(taskId: string): Promise<void>;
  closeAll(): Promise<void>;
}

interface BrowserContextLookup {
  getContext(taskId: string): { page: Page } | undefined;
}

interface CheckoutProfilePreparer {
  prepare(page: Page, profile: AresProfile): Promise<SemanticCheckoutPreparationResult>;
}

interface PaymentPreparer {
  prepare(page: Page, session?: CheckoutPaymentSession): Promise<PaymentPreparationResult>;
}

interface CheckoutJourney {
  isReadyForFinalSubmit(page: Page): Promise<boolean>;
  advanceCheckout(page: Page): Promise<boolean>;
  submitOrder(page: Page, canPurchase: () => boolean): Promise<boolean>;
  isOrderConfirmed?(page: Page): Promise<boolean>;
}

interface ActiveCheckout {
  task: Task;
  controller: AbortController;
  purchaseReady: boolean;
}

export class ShopifyPurchaseReadyExecutor implements ITaskExecutor {
  private readonly active = new Map<string, ActiveCheckout>();
  private allowFinalPurchase = false;

  constructor(
    private readonly delegate: ShopifyCheckoutBaseExecutor,
    private readonly runtime: BrowserContextLookup,
    private readonly onTaskUpdate: (task: Task) => void = () => undefined,
    private readonly checkoutPreparer: CheckoutProfilePreparer = new SemanticCheckoutPreparer(),
    private readonly paymentPreparer: PaymentPreparer = new CheckoutPaymentPreparer(),
    private readonly journey: CheckoutJourney = new ShopifyCheckoutJourney()
  ) {}

  async execute(task: Task, profile?: AresProfile, paymentSession?: CheckoutPaymentSession): Promise<boolean> {
    if (!profile) {
      task.lastError = "Shopify Purchase-Ready-Flow benötigt das zugeordnete Profil.";
      return false;
    }
    if (this.active.has(task.id)) {
      task.lastError = `Shopify Checkout ${task.id} läuft bereits.`;
      return false;
    }

    const checkoutOpened = await this.delegate.execute(task);
    if (!checkoutOpened) return false;

    const page = this.runtime.getContext(task.id)?.page;
    if (!page || page.isClosed()) {
      task.lastError = "Shopify Checkout-Kontext ist nach der Profilvorbereitung nicht mehr aktiv.";
      return false;
    }

    const active: ActiveCheckout = { task, controller: new AbortController(), purchaseReady: false };
    this.active.set(task.id, active);

    try {
      this.publishFlow(task, "checkout");
      await this.prepareUntilPurchaseReady(task, active, page, profile, paymentSession);
      if (active.controller.signal.aborted) return true;

      active.purchaseReady = true;
      this.publishFlow(task, "purchase-ready");
      this.publishFinalPurchaseStatus(task, this.allowFinalPurchase ? "armed" : "blocked");

      while (!active.controller.signal.aborted) {
        if (!this.allowFinalPurchase) {
          await this.delay(250, active.controller.signal);
          continue;
        }

        const submitted = await this.journey.submitOrder(
          page,
          () => this.allowFinalPurchase && !active.controller.signal.aborted
        );
        if (submitted) {
          this.publishFlow(task, "submitted");
          this.publishFinalPurchaseStatus(task, "submitted");

          const recovery = await confirmFinalSubmitWithRetries(
            () => this.journey.isOrderConfirmed?.(page) ?? Promise.resolve(false),
            {
              attempts: this.orderConfirmationAttempts(task),
              delayMs: this.orderConfirmationRetryDelayMs(task),
              signal: active.controller.signal
            }
          );
          if (recovery.confirmed) {
            this.publishFlow(task, "confirmed");
            this.publishFinalPurchaseStatus(task, "confirmed");
            return true;
          }

          task.lastError = "Finaler Bestell-Submit wurde ausgelöst, aber kein bestätigter Bestellerfolg erkannt. Nach zwei Recovery-Versuchen wird aus Sicherheitsgründen nicht erneut abgesendet.";
          this.blockRetryAfterAmbiguousSubmit(task, recovery.attempts, recovery.maxAttempts);
          this.publishFinalPurchaseStatus(task, "confirmation-missing");
          this.onTaskUpdate(task);
          return false;
        }

        this.publishFinalPurchaseStatus(task, this.allowFinalPurchase ? "not-ready" : "blocked");
        if (!await this.journey.isReadyForFinalSubmit(page).catch(() => false)) {
          active.purchaseReady = false;
          await this.prepareUntilPurchaseReady(task, active, page, profile, paymentSession);
          if (active.controller.signal.aborted) return true;
          active.purchaseReady = true;
          this.publishFlow(task, "purchase-ready");
        }
        await this.delay(700, active.controller.signal);
      }
      return true;
    } catch (error) {
      if (active.controller.signal.aborted) return true;
      task.lastError = error instanceof Error ? error.message : String(error);
      this.onTaskUpdate(task);
      await this.delegate.closeTask(task.id).catch(() => undefined);
      return false;
    } finally {
      this.active.delete(task.id);
    }
  }

  async setFinalPurchaseAllowed(allowed: boolean): Promise<void> {
    this.allowFinalPurchase = allowed === true;
    for (const active of this.active.values()) {
      if (!active.purchaseReady) continue;
      this.publishFinalPurchaseStatus(active.task, this.allowFinalPurchase ? "armed" : "blocked");
    }
  }

  async cancelTask(taskId: string): Promise<void> {
    this.active.get(taskId)?.controller.abort();
    this.active.delete(taskId);
    await this.delegate.closeTask(taskId);
  }

  async closeAll(): Promise<void> {
    for (const active of this.active.values()) active.controller.abort();
    this.active.clear();
    await this.delegate.closeAll();
  }

  private async prepareUntilPurchaseReady(
    task: Task,
    active: ActiveCheckout,
    page: Page,
    profile: AresProfile,
    paymentSession?: CheckoutPaymentSession
  ): Promise<void> {
    const deadline = Date.now() + this.checkoutPreparationMaxMs(task);
    const shopify = task.config.data?.["shopify"] as Record<string, unknown> | undefined;
    const initialProfile = shopify?.["checkoutProfile"] as Record<string, unknown> | undefined;
    let profileReady = initialProfile?.["requiredTargetsSatisfied"] === true;
    let lastProfile: SemanticCheckoutPreparationResult | undefined;
    let lastPayment: PaymentPreparationResult | undefined;
    let lastPaymentReason: PaymentReadinessReason = paymentSession ? "missing-preparation" : "missing-session";

    while (!active.controller.signal.aborted && Date.now() < deadline) {
      lastProfile = await this.checkoutPreparer.prepare(page, profile).catch(() => undefined);
      if (lastProfile && lastProfile.requiredTargetCount > 0) profileReady = lastProfile.requiredTargetsSatisfied;

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
      const finalControlReady = await this.journey.isReadyForFinalSubmit(page).catch(() => false);
      const reviewReady = profileReady && paymentReadiness.ready && finalControlReady;
      this.publishCheckoutPreparation(task, {
        phase: reviewReady ? "purchase-ready" : "preparing",
        profileReady,
        paymentReady: paymentReadiness.ready,
        paymentReadinessReason: paymentReadiness.reason,
        reviewReady,
        profile: lastProfile,
        payment: lastPayment
      });
      if (reviewReady) return;

      const advanced = await this.journey.advanceCheckout(page).catch(() => false);
      if (!advanced) await this.delay(700, active.controller.signal);
    }

    if (active.controller.signal.aborted) return;
    const reason = !profileReady
      ? "Shopify Checkout-Adresse/Profil wurde nicht vollständig bestätigt."
      : lastPaymentReason !== "ready"
        ? `Shopify Checkout-Zahlung ist nicht kaufbereit (${lastPaymentReason}).`
        : "Shopify Checkout erreichte keinen finalen kaufbereiten Review-/Submit-Zustand.";
    throw new Error(reason);
  }

  private publishCheckoutPreparation(task: Task, input: {
    phase: "preparing" | "purchase-ready";
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
    this.onTaskUpdate(task);
  }

  private publishFlow(task: Task, stage: "checkout" | "purchase-ready" | "submitted" | "confirmed"): void {
    const now = new Date().toISOString();
    const shopify = task.config.data?.["shopify"] as Record<string, unknown> | undefined;
    task.config.data = {
      ...(task.config.data ?? {}),
      shopifyFlow: { stage, updatedAt: now },
      ...(stage === "submitted" || stage === "confirmed" ? {
        shopify: {
          ...(shopify ?? {}),
          finalPaymentSubmitted: true,
          ...(stage === "confirmed" ? { finalPaymentConfirmed: true } : {})
        }
      } : {})
    };
    this.onTaskUpdate(task);
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
    this.onTaskUpdate(task);
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

  private checkoutPreparationMaxMs(task: Task): number {
    const raw = Number(task.config.data?.["checkoutPreparationMaxMs"] ?? 10 * 60_000);
    return Number.isFinite(raw) ? Math.min(30 * 60_000, Math.max(30_000, raw)) : 10 * 60_000;
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
