import * as fs from "fs";
import * as path from "path";

export type CookieSameSite = "Strict" | "Lax" | "None";

export interface ProfileCookieSnapshotCookie {
  name: string;
  value: string;
  domain: string;
  path: string;
  expires: number;
  httpOnly: boolean;
  secure: boolean;
  sameSite: CookieSameSite;
  partitionKey?: string;
}

export interface ProfileCookieSnapshotSummary {
  id: string;
  profileId: string;
  name: string;
  cookieCount: number;
  createdAt: string;
  updatedAt: string;
}

interface StoredSnapshotEntry extends ProfileCookieSnapshotSummary {
  ciphertext: string;
}

interface SnapshotFile {
  version: 1;
  entries: Record<string, StoredSnapshotEntry>;
}

export interface CookieSnapshotCrypto {
  isEncryptionAvailable(): boolean;
  encryptString(value: string): Buffer;
  decryptString(value: Buffer): string;
}

function clean(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function normalizeCookie(input: ProfileCookieSnapshotCookie): ProfileCookieSnapshotCookie {
  const name = clean(input?.name);
  const domain = clean(input?.domain);
  const cookiePath = clean(input?.path) || "/";
  if (!name || !domain) throw new Error("Cookie ohne Name oder Domain kann nicht gespeichert werden.");

  const sameSite: CookieSameSite = input.sameSite === "Strict" || input.sameSite === "None" ? input.sameSite : "Lax";
  const normalized: ProfileCookieSnapshotCookie = {
    name,
    value: String(input.value ?? ""),
    domain,
    path: cookiePath,
    expires: Number.isFinite(Number(input.expires)) ? Number(input.expires) : -1,
    httpOnly: input.httpOnly === true,
    secure: input.secure === true,
    sameSite
  };
  if (clean(input.partitionKey)) normalized.partitionKey = clean(input.partitionKey);
  return normalized;
}

/**
 * Explicit, user-owned cookie snapshots.
 * Cookie payloads are OS-encrypted and never stored in Task config, SQLite or renderer state.
 */
export class ProfileCookieSnapshotVault {
  private readonly entries = new Map<string, StoredSnapshotEntry>();

  constructor(
    private readonly storagePath: string,
    private readonly crypto: CookieSnapshotCrypto
  ) {
    this.load();
  }

  isEncryptionAvailable(): boolean {
    return this.crypto.isEncryptionAvailable();
  }

  list(profileId: string): ProfileCookieSnapshotSummary[] {
    const id = this.normalizeProfileId(profileId);
    return [...this.entries.values()]
      .filter(entry => entry.profileId === id)
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
      .map(({ ciphertext: _ciphertext, ...summary }) => summary);
  }

  save(profileId: string, name: string, cookies: ProfileCookieSnapshotCookie[], snapshotId?: string): ProfileCookieSnapshotSummary {
    this.assertEncryptionAvailable();
    const ownerId = this.normalizeProfileId(profileId);
    const label = clean(name);
    if (!label) throw new Error("Snapshot-Name fehlt.");
    if (!Array.isArray(cookies) || cookies.length === 0) throw new Error("Im Profil-Browser wurden keine Cookies gefunden.");

    const normalizedCookies = cookies.map(normalizeCookie);
    const now = new Date().toISOString();
    const requestedId = clean(snapshotId);
    const id = requestedId || `cookie_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const existing = this.entries.get(id);
    if (existing && existing.profileId !== ownerId) throw new Error("Cookie-Snapshot gehört zu einem anderen Profil.");

    const ciphertext = this.crypto.encryptString(JSON.stringify(normalizedCookies)).toString("base64");
    const entry: StoredSnapshotEntry = {
      id,
      profileId: ownerId,
      name: label,
      cookieCount: normalizedCookies.length,
      createdAt: existing?.createdAt || now,
      updatedAt: now,
      ciphertext
    };
    this.entries.set(id, entry);
    this.persist();
    const { ciphertext: _ciphertext, ...summary } = entry;
    return summary;
  }

  read(profileId: string, snapshotId: string): ProfileCookieSnapshotCookie[] {
    this.assertEncryptionAvailable();
    const ownerId = this.normalizeProfileId(profileId);
    const id = clean(snapshotId);
    const entry = this.entries.get(id);
    if (!entry || entry.profileId !== ownerId) throw new Error("Cookie-Snapshot wurde für dieses Profil nicht gefunden.");

    const plaintext = this.crypto.decryptString(Buffer.from(entry.ciphertext, "base64"));
    const parsed = JSON.parse(plaintext) as ProfileCookieSnapshotCookie[];
    if (!Array.isArray(parsed)) throw new Error("Cookie-Snapshot ist beschädigt.");
    return parsed.map(normalizeCookie);
  }

  delete(profileId: string, snapshotId: string): boolean {
    const ownerId = this.normalizeProfileId(profileId);
    const id = clean(snapshotId);
    const entry = this.entries.get(id);
    if (!entry || entry.profileId !== ownerId) return false;
    const deleted = this.entries.delete(id);
    if (deleted) this.persist();
    return deleted;
  }

  deleteProfile(profileId: string): number {
    const ownerId = this.normalizeProfileId(profileId);
    let deleted = 0;
    for (const [id, entry] of this.entries) {
      if (entry.profileId !== ownerId) continue;
      this.entries.delete(id);
      deleted += 1;
    }
    if (deleted > 0) this.persist();
    return deleted;
  }

  private normalizeProfileId(profileId: string): string {
    const id = clean(profileId);
    if (!id) throw new Error("Profil-ID fehlt.");
    return id;
  }

  private assertEncryptionAvailable(): void {
    if (!this.crypto.isEncryptionAvailable()) {
      throw new Error("Betriebssystem-Verschlüsselung ist nicht verfügbar. Cookie-Snapshot wurde nicht gespeichert.");
    }
  }

  private load(): void {
    try {
      if (!fs.existsSync(this.storagePath)) return;
      const parsed = JSON.parse(fs.readFileSync(this.storagePath, "utf8")) as Partial<SnapshotFile>;
      if (parsed.version !== 1 || !parsed.entries || typeof parsed.entries !== "object") return;
      for (const [id, entry] of Object.entries(parsed.entries)) {
        if (!entry || typeof entry.ciphertext !== "string" || !entry.profileId || !entry.name) continue;
        this.entries.set(id, entry);
      }
    } catch {
      // Corrupt encrypted metadata is treated as unavailable; never fall back to plaintext cookies.
    }
  }

  private persist(): void {
    const dir = path.dirname(this.storagePath);
    fs.mkdirSync(dir, { recursive: true });
    const payload: SnapshotFile = { version: 1, entries: Object.fromEntries(this.entries.entries()) };
    const tempPath = `${this.storagePath}.tmp`;
    fs.writeFileSync(tempPath, JSON.stringify(payload, null, 2), { encoding: "utf8", mode: 0o600 });
    fs.renameSync(tempPath, this.storagePath);
  }
}
