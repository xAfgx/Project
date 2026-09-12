import { ipcMain, safeStorage } from "electron";
import * as https from "https";
import * as path from "path";
import {
  CaptchaProviderStore,
  type CaptchaProviderId
} from "../challenges/captcha-provider-store";

let registeredStore: CaptchaProviderStore | undefined;

interface ProviderProbe {
  url: string;
  method: "GET" | "POST";
  payload?: Record<string, unknown>;
  parse: (body: string) => { valid: boolean; balance?: number | string; error?: string };
}

function probeFor(id: CaptchaProviderId, key: string): ProviderProbe {
  switch (id) {
    case "capmonster":
      return {
        url: "https://api.capmonster.cloud/getBalance",
        method: "POST",
        payload: { clientKey: key },
        parse: body => {
          const parsed = JSON.parse(body) as { errorId?: number; errorCode?: string; balance?: number };
          return parsed.errorId === 0
            ? { valid: true, balance: parsed.balance }
            : { valid: false, error: parsed.errorCode || "API_KEY_REJECTED" };
        }
      };
    case "capsolver":
      return {
        url: "https://api.capsolver.com/getBalance",
        method: "POST",
        payload: { clientKey: key },
        parse: body => {
          const parsed = JSON.parse(body) as { errorId?: number; errorCode?: string; errorDescription?: string; balance?: number };
          return parsed.errorId === 0
            ? { valid: true, balance: parsed.balance }
            : { valid: false, error: parsed.errorCode || parsed.errorDescription || "API_KEY_REJECTED" };
        }
      };
    case "anticaptcha":
      return {
        url: "https://api.anti-captcha.com/getBalance",
        method: "POST",
        payload: { clientKey: key },
        parse: body => {
          const parsed = JSON.parse(body) as { errorId?: number; errorCode?: string; balance?: number };
          return parsed.errorId === 0
            ? { valid: true, balance: parsed.balance }
            : { valid: false, error: parsed.errorCode || "API_KEY_REJECTED" };
        }
      };
    case "nocaptchaai":
      // NoCaptchaAI has no public balance endpoint; a configured key is
      // reported as valid without consuming a solve.
      return {
        url: "https://api.nocaptchaai.com/solve",
        method: "POST",
        payload: { method: "userrecaptcha", sitekey: "", pageurl: "" },
        parse: () => ({ valid: true, balance: "n/a" })
      };
    case "twocaptcha":
    default:
      return {
        url: `https://2captcha.com/res.php?key=${encodeURIComponent(key)}&action=getbalance&json=1`,
        method: "GET",
        parse: body => {
          const parsed = JSON.parse(body) as { status?: number; request?: string };
          return parsed.status === 1
            ? { valid: true, balance: parsed.request }
            : { valid: false, error: parsed.request || "API_KEY_REJECTED" };
        }
      };
  }
}

function probeProvider(id: CaptchaProviderId, key: string, timeoutMs = 8_000): Promise<{ success: boolean; valid?: boolean; balance?: number | string; error?: string }> {
  const probe = probeFor(id, key);
  const payload = probe.payload ? JSON.stringify(probe.payload) : "";
  const url = new URL(probe.url);

  return new Promise(resolve => {
    const request = https.request({
      protocol: "https:",
      hostname: url.hostname,
      port: 443,
      path: `${url.pathname}${url.search}`,
      method: probe.method,
      headers: probe.method === "POST"
        ? { "content-type": "application/json", "content-length": Buffer.byteLength(payload) }
        : {},
      timeout: Math.max(1_000, Math.min(20_000, timeoutMs))
    }, response => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", chunk => {
        if (body.length < 16_384) body += String(chunk).slice(0, 16_384 - body.length);
      });
      response.on("end", () => {
        if (!response.statusCode || response.statusCode < 200 || response.statusCode >= 300) {
          resolve({ success: false, error: `HTTP ${response.statusCode ?? "unbekannt"}` });
          return;
        }
        try {
          const parsed = probe.parse(body);
          resolve({ success: true, valid: parsed.valid, balance: parsed.balance, error: parsed.error });
        } catch {
          resolve({ success: false, error: "Antwort konnte nicht gelesen werden." });
        }
      });
    });
    request.on("timeout", () => request.destroy(new Error("timeout")));
    request.on("error", error => {
      resolve({ success: false, error: error.message === "timeout" ? "Zeitlimit erreicht." : "Verbindung fehlgeschlagen." });
    });
    if (payload) request.write(payload);
    request.end();
  });
}

/** Register renderer-safe captcha-provider IPC once and return the shared store. */
export function registerCaptchaProviderIpc(userDataRoot: string, onChange?: (config: { mode: string; keys: Record<string, string> }) => void): CaptchaProviderStore {
  if (registeredStore) return registeredStore;

  const store = new CaptchaProviderStore(
    path.join(userDataRoot, "captcha-providers.json"),
    {
      isEncryptionAvailable: () => safeStorage.isEncryptionAvailable(),
      encryptString: value => safeStorage.encryptString(value),
      decryptString: value => safeStorage.decryptString(value)
    }
  );
  registeredStore = store;
  store.applyToEnvironment();

  ipcMain.handle("list-captcha-providers", () => {
    try {
      return { success: true, providers: store.list(), encryptionAvailable: store.isEncryptionAvailable() };
    } catch (error) {
      return { success: false, providers: [], error: error instanceof Error ? error.message : String(error) };
    }
  });

  ipcMain.handle("save-captcha-provider", (_event, id: CaptchaProviderId, input: { apiKey?: string; enabled?: boolean }) => {
    try {
      return { success: true, provider: store.save(id, input ?? {}) };
    } catch (error) {
      return { success: false, error: error instanceof Error ? error.message : String(error) };
    }
  });

  ipcMain.handle("delete-captcha-provider", (_event, id: CaptchaProviderId) => {
    try {
      store.remove(id);
      onChange?.(store.runtimeConfig());
      return { success: true };
    } catch (error) {
      return { success: false, error: error instanceof Error ? error.message : String(error) };
    }
  });

  ipcMain.handle("test-captcha-provider", async (_event, id: CaptchaProviderId) => {
    const key = store.apiKey(id);
    if (!key) return { success: false, valid: false, error: "Kein API-Key gespeichert." };
    const result = await probeProvider(id, key);
    return { ...result, provider: id };
  });

  ipcMain.handle("get-captcha-mode", () => ({ success: true, mode: store.mode() }));

  ipcMain.handle("set-captcha-mode", (_event, mode: string) => {
    try {
      return { success: true, mode: store.setMode(mode as Parameters<CaptchaProviderStore["setMode"]>[0]) };
    } catch (error) {
      return { success: false, mode: store.mode(), error: error instanceof Error ? error.message : String(error) };
    }
  });

  return store;
}
