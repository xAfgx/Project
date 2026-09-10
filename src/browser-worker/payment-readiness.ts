import type { CheckoutPaymentSession, PaymentPreparationResult } from "../payments/models";

const REQUIRED_CARD_FIELDS = ["cardNumber", "expiry", "securityCode"] as const;

export type PaymentReadinessReason =
  | "ready"
  | "missing-session"
  | "missing-preparation"
  | "method-not-selected"
  | "missing-card-data"
  | "missing-card-fields"
  | "user-action-required";

export interface PaymentReadiness {
  ready: boolean;
  reason: PaymentReadinessReason;
}

/**
 * Pure fail-closed purchase-readiness check for the already-prepared payment state.
 * No payment values are persisted or returned; only structural readiness is evaluated.
 */
export function evaluatePaymentReadiness(
  session?: CheckoutPaymentSession,
  preparation?: PaymentPreparationResult
): PaymentReadiness {
  if (!session) return { ready: false, reason: "missing-session" };
  if (!preparation) return { ready: false, reason: "missing-preparation" };
  if (preparation.selectedMethod !== session.method) {
    return { ready: false, reason: "method-not-selected" };
  }
  if (preparation.missingFields.length > 0) {
    return { ready: false, reason: "missing-card-fields" };
  }

  if (session.method === "card") {
    const card = session.card;
    if (!card || REQUIRED_CARD_FIELDS.some(field => !String(card[field] ?? "").trim())) {
      return { ready: false, reason: "missing-card-data" };
    }
    const filled = new Set(preparation.filledFields);
    if (REQUIRED_CARD_FIELDS.some(field => !filled.has(field))) {
      return { ready: false, reason: "missing-card-fields" };
    }
    return { ready: true, reason: "ready" };
  }

  if (preparation.requiresUserAction) {
    return { ready: false, reason: "user-action-required" };
  }
  return { ready: true, reason: "ready" };
}
