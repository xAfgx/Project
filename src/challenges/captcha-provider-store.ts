import * as fs from "fs";
import * as path from "path";

export type CaptchaProviderId = "capmonster" | "twocaptcha" | "capsolver" | "anticaptcha" | "nocaptchaai";

export type CaptchaProviderCapability = "token" | "classify";

export interface CaptchaProviderDefinition {
  id: CaptchaProviderId;
  name: string;
  envKey: string;
  docsUrl: string;
  capabilities: CaptchaProviderCapability[];
}

export const CAPTCHA_PROVIDERS: CaptchaProviderDefinition[] = [
  { id: "capmonster", name: "CapMonster Cloud", envKey: "CAPMONSTER_API_KEY", docsUrl: "https://capmonster.cloud", capabilities: ["token"] },
  { id: "twocaptcha", name: "2Captcha", envKey: "TWOCAPTCHA_API_KEY", docsUrl: "https://2captcha.com", capabilities: ["token"] },
  { id: "capsolver", name: "CapSolver", envKey: "CAPSOLVER_API_KEY", docsUrl: "https://capsolver.com", capabilities: ["token"] },
  { id: "anticaptcha", name: "Anti-Captcha", envKey: "ANTICAPTCHA_API_KEY", docsUrl: "https://anti-captcha.com", capabilities: ["token"] },
  { id: "nocaptchaai", name: "NoCaptchaAI", envKey: "NOCAPTCHA_API_KEY", docsUrl: "https://nocaptchaai.com", capabilities: ["classify"] }
];

export type CaptchaSolveMode = "siglip" | "siglip-api" | "api";

export interface CaptchaProviderView {
  id: CaptchaProviderId;
  name: string;
  envKey: string;
  capabilities: CaptchaProviderCapability[];
  enabled: boolean;
  configured: boolean;
  maskedKey: string;
  updatedAt?: string;
}

interface CaptchaProviderEntry {
  enabled: boolean;
  ciphertext?: string;
  updatedAt: string;
}

interface CaptchaProviderFile {
  version: 1;
  mode?: CaptchaSolveMode;
  providers: Partial<Record<CaptchaProviderId, CaptchaProviderEntry>>;
}

export interface CaptchaVaultCrypto {
  isEncryptionAvailable(): boolean;
  encryptString(value: string): Buffer;
  decryptString(value: Buffer): string;
}

function normalizeKey(value: unknown): string {
  return String(value ?? "").trim().replace(/^['"]|['"]$/g, "");
}

function maskKey(key: string): string {
  if (!key) return "";
  if (key.length <= 6) return "••••";
  return `${key.slice(0, 3)}••••${key.slice(-3)}`;
}

/**
 * Stores captcha provider API keys outside the renderer, encrypted with the OS
 * keychain when available. Keys are pushed into the process environment so the
 * worker processes (and the existing CapMonster solver) can pick them up.
 */
export class CaptchaProviderStore {
  private readonly entries = new Map<CaptchaProviderId, CaptchaProviderEntry>();
  private solveMode: CaptchaSolveMode = "siglip-api";

  constructor(
    private readonly storagePath: string,
    private readonly crypto: CaptchaVaultCrypto
  ) {
    this.load();
  }

  isEncryptionAvailable(): boolean {
    return this.crypto.isEncryptionAvailable();
  }

  mode(): CaptchaSolveMode {
    return this.solveMode;
  }

  /** Snapshot pushed to the worker processes so changes apply live. */
  runtimeConfig(): { mode: CaptchaSolveMode; keys: Record<string, string> } {
    const keys: Record<string, string> = {};
    for (const definition of CAPTCHA_PROVIDERS) {
      const entry = this.entries.get(definition.id);
      const key = entry?.enabled ? this.apiKey(definition.id) : '';
      keys[definition.envKey] = key;
    }
    return { mode: this.solveMode, keys };
  }

  setMode(mode: CaptchaSolveMode): CaptchaSolveMode {
    this.solveMode = mode === "siglip" || mode === "api" ? mode : "siglip-api";
    this.persist();
    this.applyToEnvironment();
    return this.solveMode;
  }

  list(): CaptchaProviderView[] {
    return CAPTCHA_PROVIDERS.map(definition => {
      const entry = this.entries.get(definition.id);
      const key = this.apiKey(definition.id);
      return {
        id: definition.id,
        name: definition.name,
        envKey: definition.envKey,
        capabilities: [...definition.capabilities],
        enabled: entry?.enabled === true,
        configured: Boolean(key),
        maskedKey: maskKey(key),
        updatedAt: entry?.updatedAt
      };
    });
  }

  apiKey(id: CaptchaProviderId): string {
    const entry = this.entries.get(id);
    if (!entry?.ciphertext) return "";
    if (!this.crypto.isEncryptionAvailable()) return "";
    try {
      return normalizeKey(this.crypto.decryptString(Buffer.from(entry.ciphertext, "base64")));
    } catch {
      return "";
    }
  }

  save(id: CaptchaProviderId, input: { apiKey?: string; enabled?: boolean }): CaptchaProviderView {
    const existing = this.entries.get(id);
    const requested = normalizeKey(input.apiKey);
    const key = requested || this.apiKey(id);

    if (!key && input.enabled !== false) {
      throw new Error("API-Key ist erforderlich.");
    }
    if (key && !this.crypto.isEncryptionAvailable()) {
      throw new Error("Betriebssystem-Verschlüsselung ist nicht verfügbar. Der Key wurde nicht gespeichert.");
    }

    const entry: CaptchaProviderEntry = {
      enabled: input.enabled ?? existing?.enabled ?? true,
      updatedAt: new Date().toISOString()
    };
    if (key) entry.ciphertext = this.crypto.encryptString(key).toString("base64");
    this.entries.set(id, entry);
    this.persist();
    this.applyToEnvironment();
    return this.list().find(provider => provider.id === id) as CaptchaProviderView;
  }

  remove(id: CaptchaProviderId): void {
    this.entries.delete(id);
    this.persist();
    this.applyToEnvironment();
  }

  /** Push every configured, enabled key into process.env for workers/solvers. */
  applyToEnvironment(): void {
    for (const definition of CAPTCHA_PROVIDERS) {
      const entry = this.entries.get(definition.id);
      const key = entry?.enabled ? this.apiKey(definition.id) : "";
      // Only override; never delete keys provided by .env files so the legacy
      // CAPMONSTER_API_KEY flow keeps working as a fallback.
      if (key) process.env[definition.envKey] = key;
    }
    process.env["ARES_CAPTCHA_MODE"] = this.solveMode;
  }

  private load(): void {
    try {
      if (!fs.existsSync(this.storagePath)) return;
      const parsed = JSON.parse(fs.readFileSync(this.storagePath, "utf8")) as Partial<CaptchaProviderFile>;
      if (parsed.version !== 1 || !parsed.providers || typeof parsed.providers !== "object") return;
      if (parsed.mode === "siglip" || parsed.mode === "siglip-api" || parsed.mode === "api") this.solveMode = parsed.mode;
      for (const definition of CAPTCHA_PROVIDERS) {
        const entry = parsed.providers[definition.id];
        if (!entry || typeof entry !== "object") continue;
        this.entries.set(definition.id, {
          enabled: entry.enabled === true,
          ciphertext: typeof entry.ciphertext === "string" ? entry.ciphertext : undefined,
          updatedAt: typeof entry.updatedAt === "string" ? entry.updatedAt : new Date().toISOString()
        });
      }
    } catch {
      // A corrupt file is treated as unconfigured; never fall back to plaintext.
    }
  }

  private persist(): void {
    const dir = path.dirname(this.storagePath);
    fs.mkdirSync(dir, { recursive: true });
    const payload: CaptchaProviderFile = {
      version: 1,
      mode: this.solveMode,
      providers: Object.fromEntries(this.entries.entries())
    };
    const tempPath = `${this.storagePath}.tmp`;
    fs.writeFileSync(tempPath, JSON.stringify(payload, null, 2), { encoding: "utf8", mode: 0o600 });
    fs.renameSync(tempPath, this.storagePath);
  }
}
