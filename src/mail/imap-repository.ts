import * as fs from "fs";
import * as path from "path";
import type { ImapMailboxConfig } from "./models";

export interface MailSecretCrypto {
  isEncryptionAvailable(): boolean;
  encryptString(value: string): Buffer;
  decryptString(value: Buffer): string;
}

interface StoredMailbox {
  id: string;
  name: string;
  host: string;
  port: number;
  secure: boolean;
  user: string;
  mailbox?: string;
  /** Base64 safeStorage blob; never the plaintext password. */
  password: string;
}

/** Public shape: the password is never sent back to the UI. */
export interface MailboxSummary {
  id: string;
  name: string;
  host: string;
  port: number;
  secure: boolean;
  user: string;
  mailbox?: string;
  hasPassword: boolean;
}

function normalize(input: Partial<ImapMailboxConfig> & { id?: string }): { config: Omit<ImapMailboxConfig, "password">; password: string } | undefined {
  const id = String(input.id ?? "").trim();
  const host = String(input.host ?? "").trim();
  const user = String(input.user ?? "").trim();
  const port = Number(input.port ?? 993);
  if (!id || !host || !user) return undefined;
  if (!Number.isInteger(port) || port < 1 || port > 65535) return undefined;
  return {
    config: {
      id,
      name: String(input.name ?? id).trim() || id,
      host,
      port,
      secure: input.secure !== false,
      user,
      mailbox: input.mailbox?.trim() || undefined
    },
    password: String(input.password ?? "")
  };
}

/**
 * Stores generic IMAP mailbox configs. The password is OS-encrypted via the
 * injected crypto (Electron safeStorage); everything else is plain JSON.
 */
export class ImapMailboxRepository {
  private readonly entries = new Map<string, StoredMailbox>();

  constructor(private readonly storagePath: string, private readonly crypto: MailSecretCrypto) {
    this.loadFromDisk();
  }

  isEncryptionAvailable(): boolean {
    return this.crypto.isEncryptionAvailable();
  }

  list(): MailboxSummary[] {
    return [...this.entries.values()].map(entry => ({
      id: entry.id,
      name: entry.name,
      host: entry.host,
      port: entry.port,
      secure: entry.secure,
      user: entry.user,
      mailbox: entry.mailbox,
      hasPassword: Boolean(entry.password)
    }));
  }

  /** Full config including the decrypted password; internal use only. */
  get(id: string): ImapMailboxConfig | undefined {
    const entry = this.entries.get(id);
    if (!entry) return undefined;
    return {
      id: entry.id,
      name: entry.name,
      host: entry.host,
      port: entry.port,
      secure: entry.secure,
      user: entry.user,
      mailbox: entry.mailbox,
      password: this.decrypt(entry.password)
    };
  }

  save(input: Partial<ImapMailboxConfig> & { id?: string }): MailboxSummary {
    const normalized = normalize(input);
    if (!normalized) throw new Error("Ungültige IMAP-Konfiguration (id, host und user sind Pflicht).");
    const previous = this.entries.get(normalized.config.id);
    const password = normalized.password
      ? this.encrypt(normalized.password)
      : previous?.password ?? "";
    const stored: StoredMailbox = { ...normalized.config, password };
    this.entries.set(stored.id, stored);
    this.persistToDisk();
    return this.list().find(entry => entry.id === stored.id)!;
  }

  delete(id: string): boolean {
    const deleted = this.entries.delete(id);
    if (deleted) this.persistToDisk();
    return deleted;
  }

  private encrypt(value: string): string {
    if (!value) return "";
    if (!this.crypto.isEncryptionAvailable()) throw new Error("OS-Verschlüsselung ist nicht verfügbar; IMAP-Passwort kann nicht sicher gespeichert werden.");
    return this.crypto.encryptString(value).toString("base64");
  }

  private decrypt(value: string): string {
    if (!value) return "";
    try {
      return this.crypto.decryptString(Buffer.from(value, "base64"));
    } catch {
      return "";
    }
  }

  private loadFromDisk(): void {
    this.entries.clear();
    try {
      if (!fs.existsSync(this.storagePath)) return;
      const items = JSON.parse(fs.readFileSync(this.storagePath, "utf8"));
      if (!Array.isArray(items)) return;
      for (const item of items) {
        const id = String(item?.id ?? "").trim();
        const host = String(item?.host ?? "").trim();
        const user = String(item?.user ?? "").trim();
        const port = Number(item?.port ?? 993);
        if (!id || !host || !user || !Number.isInteger(port)) continue;
        this.entries.set(id, {
          id,
          name: String(item?.name ?? id),
          host,
          port,
          secure: item?.secure !== false,
          user,
          mailbox: typeof item?.mailbox === "string" ? item.mailbox : undefined,
          password: String(item?.password ?? "")
        });
      }
    } catch {
      // Corrupt mail storage must not prevent ARES startup.
    }
  }

  private persistToDisk(): void {
    const dir = path.dirname(this.storagePath);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(this.storagePath, JSON.stringify([...this.entries.values()], null, 2), "utf8");
  }
}
