import * as fs from "fs";
import * as path from "path";
import type { CheckoutPaymentSession, StoredPaymentPreference } from "./models";

export interface ProfileCardAutofill {
  holderName: string;
  cardNumber: string;
  expiryMonth: string;
  expiryYear: string;
  securityCode: string;
}

export interface ProfilePaymentCardDraft {
  holderName?: string;
  cardNumber?: string;
  expiryMonth?: string;
  expiryYear?: string;
  securityCode?: string;
}

export interface ProfilePaymentCardView {
  configured: boolean;
  holderName?: string;
  maskedCardNumber?: string;
  expiryMonth?: string;
  expiryYear?: string;
  securityCodeStored?: boolean;
  updatedAt?: string;
}

export interface PaymentVaultCrypto {
  isEncryptionAvailable(): boolean;
  encryptString(value: string): Buffer;
  decryptString(value: Buffer): string;
}

interface PaymentVaultEntry {
  ciphertext: string;
  updatedAt: string;
}

interface PaymentVaultCardEntry extends PaymentVaultEntry {
  label?: string;
}

interface PaymentVaultFile {
  version: 1;
  entries: Record<string, PaymentVaultEntry>;
}

interface PaymentVaultFileV2 {
  version: 2;
  cards: Record<string, PaymentVaultCardEntry>;
  assignments: Record<string, string>;
  entries?: Record<string, PaymentVaultEntry>;
}

export interface ProfilePaymentCardSummary {
  id: string;
  label: string;
  view: ProfilePaymentCardView;
}

const MASKED_CARD_PATTERN = /[•*xX]/;

function clean(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function normalizeCardNumber(value: string): string {
  const digits = value.replace(/[^0-9]/g, "");
  if (digits.length < 12 || digits.length > 19) {
    throw new Error("Kartennummer muss 12 bis 19 Ziffern enthalten.");
  }
  return digits;
}

function normalizeExpiryMonth(value: string): string {
  const month = Number(value.replace(/\D/g, ""));
  if (!Number.isInteger(month) || month < 1 || month > 12) {
    throw new Error("Ablaufmonat muss zwischen 01 und 12 liegen.");
  }
  return String(month).padStart(2, "0");
}

function normalizeExpiryYear(value: string): string {
  const digits = value.replace(/\D/g, "");
  if (digits.length === 2) return `20${digits}`;
  if (digits.length === 4 && Number(digits) >= 2000 && Number(digits) <= 2199) return digits;
  throw new Error("Ablaufjahr muss zweistellig oder vierstellig angegeben werden.");
}

function normalizeSecurityCode(value: string): string {
  const digits = value.replace(/\D/g, "");
  if (digits.length < 3 || digits.length > 4) {
    throw new Error("CVC/CVV muss 3 oder 4 Ziffern enthalten.");
  }
  return digits;
}

function materializeExpiry(month: string, year: string): string {
  return `${month}/${year.slice(-2)}`;
}

function maskCardNumber(cardNumber: string): string {
  const last4 = cardNumber.slice(-4);
  return `•••• •••• •••• ${last4}`;
}

/**
 * Stores profile payment secrets outside profiles.json as one OS-encrypted blob per profile.
 * The renderer only receives a masked view; plaintext secrets are returned only when a
 * checkout session is materialized inside Electron main.
 */
export class ProfilePaymentVault {
  private readonly entries = new Map<string, PaymentVaultEntry>();
  private readonly cards = new Map<string, PaymentVaultCardEntry>();
  private readonly assignments = new Map<string, string>();

  constructor(
    private readonly storagePath: string,
    private readonly crypto: PaymentVaultCrypto
  ) {
    this.load();
  }

  isEncryptionAvailable(): boolean {
    return this.crypto.isEncryptionAvailable();
  }

  /**
   * Legacy per-profile save: stores the card under the profile id and assigns
   * it to that profile, so the card manager can later share it with others.
   */
  save(profileId: string, draft: ProfilePaymentCardDraft): ProfilePaymentCardView {
    const id = this.normalizeProfileId(profileId);
    const view = this.upsertCard(id, draft);
    this.assignments.set(id, id);
    this.persist();
    return view;
  }

  /** Create or update a named card in the shared card registry. */
  saveCard(cardId: string, draft: ProfilePaymentCardDraft, label?: string): ProfilePaymentCardView {
    const id = this.normalizeProfileId(cardId);
    const view = this.upsertCard(id, draft, label);
    this.persist();
    return view;
  }

  listCards(): ProfilePaymentCardSummary[] {
    const summaries: ProfilePaymentCardSummary[] = [];
    for (const [id, entry] of this.cards.entries()) {
      try {
        const secret = this.decrypt(entry);
        summaries.push({
          id,
          label: entry.label?.trim() || maskCardNumber(secret.cardNumber),
          view: this.toView(secret, entry.updatedAt)
        });
      } catch {
        // Skip unreadable entries instead of failing the whole list.
      }
    }
    return summaries.sort((a, b) => a.label.localeCompare(b.label));
  }

  deleteCard(cardId: string): boolean {
    const id = this.normalizeProfileId(cardId);
    const deleted = this.cards.delete(id);
    let changed = deleted;
    for (const [profileId, assigned] of [...this.assignments.entries()]) {
      if (assigned === id) {
        this.assignments.delete(profileId);
        changed = true;
      }
    }
    if (changed) this.persist();
    return deleted;
  }

  /** Assign a shared card to a profile (or clear the assignment with null). */
  assignCard(profileId: string, cardId: string | null): void {
    const id = this.normalizeProfileId(profileId);
    const card = String(cardId ?? "").trim();
    if (!card) {
      this.assignments.delete(id);
      this.persist();
      return;
    }
    if (!this.cards.has(card)) throw new Error("Die ausgewählte Karte existiert nicht.");
    this.assignments.set(id, card);
    this.persist();
  }

  /** Effective card id for a profile: explicit assignment, else legacy entry. */
  assignedCardId(profileId: string): string {
    const id = this.normalizeProfileId(profileId);
    const assigned = this.assignments.get(id);
    if (assigned && this.cards.has(assigned)) return assigned;
    return this.entries.has(id) ? id : "";
  }

  getView(profileId: string): ProfilePaymentCardView {
    const id = this.normalizeProfileId(profileId);
    const resolved = this.resolveEntry(id);
    if (!resolved) return { configured: false };
    this.assertEncryptionAvailable();
    const secret = this.decrypt(resolved.entry);
    return this.toView(secret, resolved.entry.updatedAt);
  }

  toCheckoutPaymentSession(profileId: string, preference?: StoredPaymentPreference): CheckoutPaymentSession {
    const method = preference?.method ?? "card";
    const session: CheckoutPaymentSession = {
      method,
      label: preference?.label?.trim() || undefined
    };
    if (method !== "card") return session;

    const id = this.normalizeProfileId(profileId);
    const resolved = this.resolveEntry(id);
    if (!resolved) throw new Error("Für dieses Profil sind keine verschlüsselten Kartendaten gespeichert.");
    this.assertEncryptionAvailable();
    const secret = this.decrypt(resolved.entry);
    session.card = {
      holderName: secret.holderName,
      cardNumber: secret.cardNumber,
      expiry: materializeExpiry(secret.expiryMonth, secret.expiryYear),
      securityCode: secret.securityCode
    };
    return session;
  }

  /**
   * Profile deleted: drop the assignment and any legacy profile-owned entry.
   * Shared cards created through the manager stay available for other profiles.
   */
  delete(profileId: string): boolean {
    const id = this.normalizeProfileId(profileId);
    const deletedEntry = this.entries.delete(id);
    const unassigned = this.assignments.delete(id);
    const ownedCard = this.cards.delete(id);
    if (deletedEntry || unassigned || ownedCard) this.persist();
    return deletedEntry || ownedCard;
  }

  private resolveEntry(profileId: string): { entry: PaymentVaultEntry; cardId: string } | undefined {
    const assigned = this.assignments.get(profileId);
    if (assigned) {
      const card = this.cards.get(assigned);
      if (card) return { entry: card, cardId: assigned };
    }
    const legacy = this.entries.get(profileId);
    if (legacy) return { entry: legacy, cardId: profileId };
    return undefined;
  }

  private upsertCard(cardId: string, draft: ProfilePaymentCardDraft, label?: string): ProfilePaymentCardView {
    this.assertEncryptionAvailable();
    const id = this.normalizeProfileId(cardId);
    const existing = this.readCardSecret(id);

    const requestedCardNumber = clean(draft.cardNumber);
    const cardNumber = !requestedCardNumber || MASKED_CARD_PATTERN.test(requestedCardNumber)
      ? existing?.cardNumber ?? ""
      : normalizeCardNumber(requestedCardNumber);

    const holderName = clean(draft.holderName) || existing?.holderName || "";
    const expiryMonth = clean(draft.expiryMonth)
      ? normalizeExpiryMonth(clean(draft.expiryMonth))
      : existing?.expiryMonth ?? "";
    const expiryYear = clean(draft.expiryYear)
      ? normalizeExpiryYear(clean(draft.expiryYear))
      : existing?.expiryYear ?? "";
    const requestedSecurityCode = clean(draft.securityCode);
    const securityCode = requestedSecurityCode
      ? normalizeSecurityCode(requestedSecurityCode)
      : existing?.securityCode ?? "";

    if (!holderName || !cardNumber || !expiryMonth || !expiryYear || !securityCode) {
      throw new Error("Karteninhaber, Kartennummer, Ablaufmonat, Ablaufjahr und CVC/CVV sind erforderlich.");
    }

    const secret: ProfileCardAutofill = { holderName, cardNumber, expiryMonth, expiryYear, securityCode };
    const updatedAt = new Date().toISOString();
    const encrypted = this.crypto.encryptString(JSON.stringify(secret));
    const previousLabel = this.cards.get(id)?.label;
    this.cards.set(id, {
      ciphertext: encrypted.toString("base64"),
      updatedAt,
      label: clean(label) || previousLabel || undefined
    });
    return this.toView(secret, updatedAt);
  }

  private readCardSecret(cardId: string): ProfileCardAutofill | undefined {
    const card = this.cards.get(cardId);
    if (card) {
      this.assertEncryptionAvailable();
      return this.decrypt(card);
    }
    return this.entries.get(cardId) ? this.readSecret(cardId) : undefined;
  }

  private normalizeProfileId(profileId: string): string {
    const id = String(profileId ?? "").trim();
    if (!id) throw new Error("Profil-ID fehlt.");
    return id;
  }

  private assertEncryptionAvailable(): void {
    if (!this.crypto.isEncryptionAvailable()) {
      throw new Error("Betriebssystem-Verschlüsselung für Zahlungsdaten ist nicht verfügbar. Zahlungsdaten wurden nicht gespeichert.");
    }
  }

  private decrypt(entry: PaymentVaultEntry): ProfileCardAutofill {
    const plaintext = this.crypto.decryptString(Buffer.from(entry.ciphertext, "base64"));
    const parsed = JSON.parse(plaintext) as Partial<ProfileCardAutofill>;
    if (!parsed.holderName || !parsed.cardNumber || !parsed.expiryMonth || !parsed.expiryYear || !parsed.securityCode) {
      throw new Error("Gespeicherte Zahlungsdaten sind unvollständig oder beschädigt.");
    }
    return {
      holderName: String(parsed.holderName),
      cardNumber: String(parsed.cardNumber),
      expiryMonth: String(parsed.expiryMonth),
      expiryYear: String(parsed.expiryYear),
      securityCode: String(parsed.securityCode)
    };
  }

  private readSecret(profileId: string): ProfileCardAutofill | undefined {
    const entry = this.entries.get(profileId);
    if (!entry) return undefined;
    this.assertEncryptionAvailable();
    return this.decrypt(entry);
  }

  private toView(secret: ProfileCardAutofill, updatedAt: string): ProfilePaymentCardView {
    return {
      configured: true,
      holderName: secret.holderName,
      maskedCardNumber: maskCardNumber(secret.cardNumber),
      expiryMonth: secret.expiryMonth,
      expiryYear: secret.expiryYear,
      securityCodeStored: Boolean(secret.securityCode),
      updatedAt
    };
  }

  private load(): void {
    try {
      if (!fs.existsSync(this.storagePath)) return;
      const parsed = JSON.parse(fs.readFileSync(this.storagePath, "utf8")) as Partial<PaymentVaultFileV2>;
      const version = Number(parsed.version);
      if (version !== 1 && version !== 2) return;
      for (const [profileId, entry] of Object.entries(parsed.entries ?? {})) {
        if (!entry || typeof entry.ciphertext !== "string" || typeof entry.updatedAt !== "string") continue;
        this.entries.set(profileId, entry);
      }
      for (const [cardId, entry] of Object.entries(parsed.cards ?? {})) {
        if (!entry || typeof entry.ciphertext !== "string" || typeof entry.updatedAt !== "string") continue;
        this.cards.set(cardId, {
          ciphertext: entry.ciphertext,
          updatedAt: entry.updatedAt,
          label: typeof entry.label === "string" ? entry.label : undefined
        });
      }
      for (const [profileId, cardId] of Object.entries(parsed.assignments ?? {})) {
        if (typeof profileId !== "string" || typeof cardId !== "string" || !profileId || !cardId) continue;
        this.assignments.set(profileId, cardId);
      }
    } catch {
      // A corrupt vault is treated as unavailable data; never fall back to plaintext storage.
    }
  }

  private persist(): void {
    const dir = path.dirname(this.storagePath);
    fs.mkdirSync(dir, { recursive: true });
    const payload: PaymentVaultFileV2 = {
      version: 2,
      cards: Object.fromEntries(this.cards.entries()),
      assignments: Object.fromEntries(this.assignments.entries()),
      ...(this.entries.size ? { entries: Object.fromEntries(this.entries.entries()) } : {})
    };
    const tempPath = `${this.storagePath}.tmp`;
    fs.writeFileSync(tempPath, JSON.stringify(payload, null, 2), { encoding: "utf8", mode: 0o600 });
    fs.renameSync(tempPath, this.storagePath);
  }
}
