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

interface PaymentVaultFile {
  version: 1;
  entries: Record<string, PaymentVaultEntry>;
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

  constructor(
    private readonly storagePath: string,
    private readonly crypto: PaymentVaultCrypto
  ) {
    this.load();
  }

  isEncryptionAvailable(): boolean {
    return this.crypto.isEncryptionAvailable();
  }

  save(profileId: string, draft: ProfilePaymentCardDraft): ProfilePaymentCardView {
    this.assertEncryptionAvailable();
    const id = this.normalizeProfileId(profileId);
    const existing = this.readSecret(id);

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

    const secret: ProfileCardAutofill = {
      holderName,
      cardNumber,
      expiryMonth,
      expiryYear,
      securityCode
    };
    const updatedAt = new Date().toISOString();
    const encrypted = this.crypto.encryptString(JSON.stringify(secret));
    this.entries.set(id, { ciphertext: encrypted.toString("base64"), updatedAt });
    this.persist();
    return this.toView(secret, updatedAt);
  }

  getView(profileId: string): ProfilePaymentCardView {
    const id = this.normalizeProfileId(profileId);
    const entry = this.entries.get(id);
    if (!entry) return { configured: false };
    this.assertEncryptionAvailable();
    const secret = this.decrypt(entry);
    return this.toView(secret, entry.updatedAt);
  }

  toCheckoutPaymentSession(profileId: string, preference?: StoredPaymentPreference): CheckoutPaymentSession {
    const method = preference?.method ?? "card";
    const session: CheckoutPaymentSession = {
      method,
      label: preference?.label?.trim() || undefined
    };
    if (method !== "card") return session;

    const id = this.normalizeProfileId(profileId);
    const entry = this.entries.get(id);
    if (!entry) throw new Error("Für dieses Profil sind keine verschlüsselten Kartendaten gespeichert.");
    this.assertEncryptionAvailable();
    const secret = this.decrypt(entry);
    session.card = {
      holderName: secret.holderName,
      cardNumber: secret.cardNumber,
      expiry: materializeExpiry(secret.expiryMonth, secret.expiryYear),
      securityCode: secret.securityCode
    };
    return session;
  }

  delete(profileId: string): boolean {
    const id = this.normalizeProfileId(profileId);
    const deleted = this.entries.delete(id);
    if (deleted) this.persist();
    return deleted;
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
      const parsed = JSON.parse(fs.readFileSync(this.storagePath, "utf8")) as Partial<PaymentVaultFile>;
      if (parsed.version !== 1 || !parsed.entries || typeof parsed.entries !== "object") return;
      for (const [profileId, entry] of Object.entries(parsed.entries)) {
        if (!entry || typeof entry.ciphertext !== "string" || typeof entry.updatedAt !== "string") continue;
        this.entries.set(profileId, entry);
      }
    } catch {
      // A corrupt vault is treated as unavailable data; never fall back to plaintext storage.
    }
  }

  private persist(): void {
    const dir = path.dirname(this.storagePath);
    fs.mkdirSync(dir, { recursive: true });
    const payload: PaymentVaultFile = {
      version: 1,
      entries: Object.fromEntries(this.entries.entries())
    };
    const tempPath = `${this.storagePath}.tmp`;
    fs.writeFileSync(tempPath, JSON.stringify(payload, null, 2), { encoding: "utf8", mode: 0o600 });
    fs.renameSync(tempPath, this.storagePath);
  }
}
