import * as crypto from "crypto";
import * as fs from "fs";
import * as path from "path";
import type { AccountRecord, AccountStatus, AccountSummary, CreateAccountInput } from "./models";

export interface AccountSecretCrypto {
  isEncryptionAvailable(): boolean;
  encryptString(value: string): Buffer;
  decryptString(value: Buffer): string;
}

interface StoredAccount {
  id: string;
  shopId: string;
  label: string;
  email: string;
  /** Base64 safeStorage blob; never the plaintext password. */
  password: string;
  status: AccountStatus;
  message?: string;
  createdAt: string;
  updatedAt: string;
}

const PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%";

function generatePassword(length = 16): string {
  const bytes = crypto.randomBytes(length);
  let out = "";
  for (let index = 0; index < length; index++) {
    out += PASSWORD_ALPHABET[bytes[index] % PASSWORD_ALPHABET.length];
  }
  return out;
}

function randomTag(length = 8): string {
  const bytes = crypto.randomBytes(length);
  let out = "";
  for (let index = 0; index < length; index++) {
    out += "abcdefghijklmnopqrstuvwxyz0123456789"[bytes[index] % 36];
  }
  return out;
}

/** Expands a `{rand}` placeholder. Without the placeholder the address is used as-is. */
export function expandAccountEmail(pattern: string): string {
  const trimmed = pattern.trim();
  if (!trimmed) return "";
  if (trimmed.includes("{rand}")) return trimmed.replace(/\{rand\}/g, randomTag());
  return trimmed;
}

export class AccountStore {
  private readonly entries = new Map<string, StoredAccount>();

  constructor(private readonly storagePath: string, private readonly crypto: AccountSecretCrypto) {
    this.loadFromDisk();
  }

  isEncryptionAvailable(): boolean {
    return this.crypto.isEncryptionAvailable();
  }

  list(): AccountSummary[] {
    return [...this.entries.values()]
      .sort((left, right) => right.createdAt.localeCompare(left.createdAt))
      .map(entry => ({
        id: entry.id,
        shopId: entry.shopId,
        label: entry.label,
        email: entry.email,
        status: entry.status,
        message: entry.message,
        createdAt: entry.createdAt,
        updatedAt: entry.updatedAt,
        hasPassword: Boolean(entry.password)
      }));
  }

  /** Full record including the decrypted password. */
  get(id: string): AccountRecord | undefined {
    const entry = this.entries.get(id);
    if (!entry) return undefined;
    return { ...entry, password: this.decrypt(entry.password) };
  }

  create(input: CreateAccountInput): AccountRecord {
    const shopId = String(input.shopId ?? "").trim();
    const email = expandAccountEmail(String(input.email ?? ""));
    if (!shopId || !email) throw new Error("Account benötigt Shop und E-Mail.");
    const password = String(input.password ?? "").trim() || generatePassword();
    const now = new Date().toISOString();
    const entry: StoredAccount = {
      id: `account-${randomTag(10)}`,
      shopId,
      label: String(input.label ?? "").trim() || email,
      email,
      password: this.encrypt(password),
      status: "pending",
      createdAt: now,
      updatedAt: now
    };
    this.entries.set(entry.id, entry);
    this.persistToDisk();
    return this.get(entry.id)!;
  }

  updateStatus(id: string, status: AccountStatus, message?: string): AccountSummary | undefined {
    const entry = this.entries.get(id);
    if (!entry) return undefined;
    entry.status = status;
    entry.message = message;
    entry.updatedAt = new Date().toISOString();
    this.persistToDisk();
    return this.list().find(item => item.id === id);
  }

  delete(id: string): boolean {
    const deleted = this.entries.delete(id);
    if (deleted) this.persistToDisk();
    return deleted;
  }

  private encrypt(value: string): string {
    if (!value) return "";
    if (!this.crypto.isEncryptionAvailable()) throw new Error("OS-Verschlüsselung ist nicht verfügbar; Account-Passwort kann nicht sicher gespeichert werden.");
    return this.crypto.encryptString(value).toString("base64");
  }

  private decrypt(value: string): string {
    if (!value) return "";
    try { return this.crypto.decryptString(Buffer.from(value, "base64")); }
    catch { return ""; }
  }

  private loadFromDisk(): void {
    this.entries.clear();
    try {
      if (!fs.existsSync(this.storagePath)) return;
      const items = JSON.parse(fs.readFileSync(this.storagePath, "utf8"));
      if (!Array.isArray(items)) return;
      for (const item of items) {
        const id = String(item?.id ?? "").trim();
        const email = String(item?.email ?? "").trim();
        if (!id || !email) continue;
        this.entries.set(id, {
          id,
          shopId: String(item?.shopId ?? ""),
          label: String(item?.label ?? email),
          email,
          password: String(item?.password ?? ""),
          status: (["pending", "created", "confirmed", "failed"].includes(item?.status) ? item.status : "pending") as AccountStatus,
          message: typeof item?.message === "string" ? item.message : undefined,
          createdAt: String(item?.createdAt ?? new Date().toISOString()),
          updatedAt: String(item?.updatedAt ?? new Date().toISOString())
        });
      }
    } catch {
      // Corrupt account storage must not prevent ARES startup.
    }
  }

  private persistToDisk(): void {
    fs.mkdirSync(path.dirname(this.storagePath), { recursive: true });
    fs.writeFileSync(this.storagePath, JSON.stringify([...this.entries.values()], null, 2), "utf8");
  }
}
