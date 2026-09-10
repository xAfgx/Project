import type { CheckoutPaymentSession, PaymentMethod } from "./models";

const ALLOWED_METHODS = new Set<PaymentMethod>(["card", "paypal", "shop-pay", "klarna", "other"]);

function text(value: unknown, maxLength: number): string | undefined {
  if (typeof value !== "string") return undefined;
  const normalized = value.trim().slice(0, maxLength);
  return normalized || undefined;
}

function compactDigits(value: unknown, maxLength: number): string | undefined {
  if (typeof value !== "string") return undefined;
  const normalized = value.replace(/\s+/g, "").slice(0, maxLength);
  return normalized || undefined;
}

/**
 * Renderer/IPC input validation for the established checkout payment contract.
 * Runtime card fields stay holderName/cardNumber/expiry/securityCode end to end.
 */
export function normalizePaymentSessionInput(input: unknown): CheckoutPaymentSession | undefined {
  if (!input || typeof input !== "object") return undefined;
  const raw = input as Record<string, unknown>;
  const method = String(raw["method"] ?? "").trim() as PaymentMethod;
  if (!ALLOWED_METHODS.has(method)) return undefined;

  const session: CheckoutPaymentSession = {
    method,
    label: text(raw["label"], 120)
  };

  if (method !== "card") return session;

  const rawCard = raw["card"];
  if (!rawCard || typeof rawCard !== "object") return session;
  const card = rawCard as Record<string, unknown>;

  session.card = {
    holderName: text(card["holderName"], 120),
    cardNumber: compactDigits(card["cardNumber"], 24),
    expiry: text(card["expiry"], 12),
    securityCode: text(card["securityCode"], 8)
  };

  return session;
}
