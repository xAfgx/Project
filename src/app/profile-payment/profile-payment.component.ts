import { Component, Input, OnChanges, SimpleChanges } from "@angular/core";
import { I18nService } from "../i18n/i18n.service";

interface PaymentView {
  configured: boolean;
  holderName?: string;
  maskedCardNumber?: string;
  expiryMonth?: string;
  expiryYear?: string;
  securityCodeStored?: boolean;
  updatedAt?: string;
}

interface PaymentCardSummary {
  id: string;
  label: string;
  view: PaymentView;
}

@Component({
  selector: "app-profile-payment",
  templateUrl: "./profile-payment.component.html",
  styleUrls: ["./profile-payment.component.scss"]
})
export class ProfilePaymentComponent implements OnChanges {
  @Input() profileId = "";

  holderName = "";
  cardNumber = "";
  expiryMonth = "";
  expiryYear = "";
  securityCode = "";
  cardLabel = "";

  configured = false;
  encryptionAvailable = true;
  saving = false;
  error = "";
  info = "";

  cards: PaymentCardSummary[] = [];
  assignedCardId = "";

  constructor(readonly i18n: I18nService) {}

  async ngOnChanges(changes: SimpleChanges): Promise<void> {
    if (changes["profileId"]) await this.refresh();
  }

  async refresh(): Promise<void> {
    await Promise.all([this.load(), this.loadCards()]);
  }

  async loadCards(): Promise<void> {
    const api = (window as any).ares;
    if (!api?.listPaymentCards) return;
    const result = await api.listPaymentCards().catch(() => undefined);
    if (result?.success) this.cards = result.cards || [];
    this.encryptionAvailable = result?.encryptionAvailable !== false;

    const id = this.profileId.trim();
    if (!id || !api?.getProfilePaymentCard) {
      this.assignedCardId = "";
      return;
    }
    const assigned = await api.getProfilePaymentCard(id).catch(() => undefined);
    this.assignedCardId = assigned?.success ? String(assigned.cardId || "") : "";
  }

  async assignCard(cardId: string): Promise<void> {
    this.error = "";
    this.info = "";
    const id = this.profileId.trim();
    if (!id) {
      this.error = this.i18n.t("Bitte zuerst eine Profil-ID vergeben.");
      return;
    }
    const api = (window as any).ares;
    if (!api?.assignProfilePaymentCard) {
      this.error = this.i18n.t("Karten-Zuweisung ist nur in der Electron-App verfügbar.");
      return;
    }
    const result = await api.assignProfilePaymentCard(id, cardId || null);
    if (!result?.success) {
      this.error = result?.error || this.i18n.t("Karte konnte nicht zugewiesen werden.");
      return;
    }
    this.assignedCardId = String(result.cardId || "");
    this.info = cardId ? this.i18n.t("Karte dem Profil zugewiesen.") : this.i18n.t("Karten-Zuweisung entfernt.");
    await this.load();
  }

  async deleteCard(cardId: string, event?: Event): Promise<void> {
    if (event) event.stopPropagation();
    this.error = "";
    this.info = "";
    const api = (window as any).ares;
    if (!api?.deletePaymentCard) return;
    if (!window.confirm(this.i18n.t("Karte wirklich löschen? Sie wird aus allen Profilen entfernt."))) return;
    const result = await api.deletePaymentCard(cardId);
    if (!result?.success) {
      this.error = result?.error || this.i18n.t("Karte konnte nicht gelöscht werden.");
      return;
    }
    this.info = this.i18n.t("Karte gelöscht.");
    await this.refresh();
  }

  async save(): Promise<void> {
    this.error = "";
    this.info = "";
    const id = this.profileId.trim();
    if (!id) {
      this.error = this.i18n.t("Bitte zuerst eine Profil-ID vergeben.");
      return;
    }

    const api = (window as any).ares;
    if (!api?.savePaymentCard && !api?.saveProfilePayment) {
      this.error = this.i18n.t("Verschlüsselter Payment Vault ist nur in der Electron-App verfügbar.");
      return;
    }

    this.saving = true;
    try {
      const draft = {
        holderName: this.holderName,
        cardNumber: this.cardNumber,
        expiryMonth: this.expiryMonth,
        expiryYear: this.expiryYear,
        securityCode: this.securityCode
      };
      if (api.savePaymentCard) {
        const cardId = this.assignedCardId || this.newCardId();
        const result = await api.savePaymentCard(cardId, draft, this.cardLabel);
        this.encryptionAvailable = result?.encryptionAvailable !== false;
        if (!result?.success) {
          this.error = result?.error || this.i18n.t("Zahlungsdaten konnten nicht gespeichert werden.");
          return;
        }
        await api.assignProfilePaymentCard?.(id, cardId);
        this.assignedCardId = cardId;
        this.applyView(result.card as PaymentView);
        this.securityCode = "";
        this.info = this.i18n.t("Zahlungsdaten verschlüsselt gespeichert.");
        await this.loadCards();
        return;
      }
      const result = await api.saveProfilePayment(id, draft);
      this.encryptionAvailable = result.encryptionAvailable !== false;
      if (!result.success) {
        this.error = result.error || this.i18n.t("Zahlungsdaten konnten nicht gespeichert werden.");
        return;
      }
      this.applyView(result.payment as PaymentView);
      this.securityCode = "";
      this.info = this.i18n.t("Zahlungsdaten verschlüsselt gespeichert.");
      await this.loadCards();
    } finally {
      this.saving = false;
    }
  }

  private newCardId(): string {
    return `card_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  }

  private async load(): Promise<void> {
    this.error = "";
    this.info = "";
    this.configured = false;
    this.holderName = "";
    this.cardNumber = "";
    this.expiryMonth = "";
    this.expiryYear = "";
    this.securityCode = "";
    this.cardLabel = "";

    const id = this.profileId.trim();
    if (!id) return;
    const api = (window as any).ares;
    if (!api?.getProfilePayment) return;

    const result = await api.getProfilePayment(id);
    this.encryptionAvailable = result.encryptionAvailable !== false;
    if (!result.success) {
      this.error = result.error || this.i18n.t("Gespeicherte Zahlungsdaten konnten nicht geladen werden.");
      return;
    }
    this.applyView(result.payment as PaymentView);
  }

  private applyView(payment?: PaymentView): void {
    if (!payment?.configured) {
      this.configured = false;
      return;
    }
    this.configured = true;
    this.holderName = payment.holderName || "";
    this.cardNumber = payment.maskedCardNumber || "";
    this.expiryMonth = payment.expiryMonth || "";
    this.expiryYear = payment.expiryYear || "";
  }
}
