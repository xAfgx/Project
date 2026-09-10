import type { Frame, Locator, Page } from "./types";
import type {
  CheckoutPaymentSession,
  PaymentMethod,
  PaymentPreparationResult
} from "../payments/models";

interface MethodDescriptor {
  method: PaymentMethod;
  patterns: RegExp[];
}

const METHODS: MethodDescriptor[] = [
  { method: "card", patterns: [/credit card/i, /debit card/i, /kreditkarte/i, /visa/i, /mastercard/i] },
  { method: "paypal", patterns: [/paypal/i] },
  { method: "shop-pay", patterns: [/shop\s*pay/i] },
  { method: "klarna", patterns: [/klarna/i] }
];

/** Generic checkout payment preparation. It never submits an order. */
export class CheckoutPaymentPreparer {
  async prepare(page: Page, session?: CheckoutPaymentSession): Promise<PaymentPreparationResult> {
    const detectedMethods = await this.detectMethods(page);
    const result: PaymentPreparationResult = {
      detectedMethods,
      selectedMethod: undefined,
      filledFields: [],
      missingFields: [],
      requiresUserAction: true
    };

    if (!session) {
      result.note = detectedMethods.length
        ? "Zahlungsarten erkannt. Keine Session-Zahlungsdaten hinterlegt."
        : "Noch keine Zahlungsart sichtbar.";
      return result;
    }

    const selected = await this.selectMethod(page, session.method, session.label);
    if (selected || session.method === "card") result.selectedMethod = session.method;

    if (session.method !== "card") {
      result.note = selected
        ? `${this.label(session.method)} ausgewählt. Externe Anmeldung/Freigabe bleibt manuell.`
        : `${this.label(session.method)} konnte noch nicht eindeutig ausgewählt werden.`;
      return result;
    }

    const card = session.card;
    if (!card) {
      result.missingFields = ["cardNumber", "expiry", "securityCode"];
      result.note = "Kartenzahlung erkannt/ausgewählt, aber keine Session-Kartendaten vorhanden.";
      return result;
    }

    await this.fillCardField(page, "holderName", card.holderName, [
      'input[autocomplete="cc-name"]',
      'input[name*="cardholder" i]',
      'input[name*="card_name" i]',
      'input[name*="name_on_card" i]'
    ], result);
    await this.fillCardField(page, "cardNumber", card.cardNumber, [
      'input[autocomplete="cc-number"]',
      'input[name*="cardnumber" i]',
      'input[name*="card_number" i]',
      'input[data-card-field="number"]'
    ], result);
    await this.fillCardField(page, "expiry", card.expiry, [
      'input[autocomplete="cc-exp"]',
      'input[name*="expiry" i]',
      'input[name*="expiration" i]',
      'input[data-card-field="expiry"]'
    ], result);
    await this.fillCardField(page, "securityCode", card.securityCode, [
      'input[autocomplete="cc-csc"]',
      'input[name*="security_code" i]',
      'input[name*="cvv" i]',
      'input[name*="cvc" i]',
      'input[data-card-field="verification_value"]'
    ], result);

    // A fully prepared card needs no additional payment-field input. The final
    // order click is still controlled separately by the global purchase guard.
    result.requiresUserAction = result.missingFields.length > 0;
    result.note = result.missingFields.length
      ? "Kartendaten teilweise vorbereitet. Fehlende Felder müssen ergänzt werden; Bestellung wird nicht abgesendet."
      : "Kartendaten vorbereitet. Der finale Submit bleibt separat geschützt.";
    return result;
  }

  private async detectMethods(page: Page): Promise<PaymentMethod[]> {
    const text = (await Promise.all(this.frames(page).map(frame => this.safeBodyText(frame)))).join("\n");
    const methods = METHODS.filter(item => item.patterns.some(pattern => pattern.test(text))).map(item => item.method);
    if (await this.hasVisibleCardField(page)) methods.push("card");
    return [...new Set(methods)];
  }

  private async hasVisibleCardField(page: Page): Promise<boolean> {
    for (const frame of this.frames(page)) {
      const locator = frame.locator('input[autocomplete="cc-number"], input[name*="cardnumber" i], input[name*="card_number" i], input[data-card-field="number"]').first();
      if (await this.isVisible(locator)) return true;
    }
    return false;
  }

  private async selectMethod(page: Page, method: PaymentMethod, preferredLabel?: string): Promise<boolean> {
    const descriptors = METHODS.find(item => item.method === method)?.patterns ?? [];
    const patterns = preferredLabel?.trim()
      ? [new RegExp(this.escapeRegex(preferredLabel.trim()), "i"), ...descriptors]
      : descriptors;

    for (const frame of this.frames(page)) {
      for (const pattern of patterns) {
        const candidates = [
          frame.getByRole("radio", { name: pattern }).first(),
          frame.locator("label").filter({ hasText: pattern }).first(),
          frame.locator('[role="radio"]').filter({ hasText: pattern }).first()
        ];
        for (const locator of candidates) {
          if (!await this.isVisible(locator)) continue;
          try {
            // Keep iframe/payment selection on the locator-native click path.
            // The SeleniumBase RPC locator owns frame/OOPIF offset resolution;
            // routing this through page-level pointer geometry would drop that context.
            await locator.click({ timeout: 1_000 });
            return true;
          } catch {}
        }
      }
    }
    return false;
  }

  private async fillCardField(
    page: Page,
    key: string,
    value: string | undefined,
    selectors: string[],
    result: PaymentPreparationResult
  ): Promise<void> {
    if (!value?.trim()) {
      if (key !== "holderName") result.missingFields.push(key);
      return;
    }

    for (const frame of this.frames(page)) {
      for (const selector of selectors) {
        const locator = frame.locator(selector).first();
        if (!await this.isVisible(locator)) continue;
        try {
          await locator.fill(value.trim(), { timeout: 1_500 });
          result.filledFields.push(key);
          return;
        } catch {}
      }
    }
    result.missingFields.push(key);
  }

  private frames(page: Page): Frame[] {
    return page.frames();
  }

  private async safeBodyText(frame: Frame): Promise<string> {
    try {
      return await frame.locator("body").innerText({ timeout: 500 });
    } catch {
      return "";
    }
  }

  private async isVisible(locator: Locator): Promise<boolean> {
    try {
      return await locator.isVisible({ timeout: 250 });
    } catch {
      return false;
    }
  }

  private label(method: PaymentMethod): string {
    const labels: Record<PaymentMethod, string> = {
      card: "Kartenzahlung",
      paypal: "PayPal",
      "shop-pay": "Shop Pay",
      klarna: "Klarna",
      other: "Zahlungsart"
    };
    return labels[method];
  }

  private escapeRegex(value: string): string {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }
}
