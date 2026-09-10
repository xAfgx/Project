import * as fs from "fs";
import * as https from "https";
import * as path from "path";

const KEY_NAME = "CAPMONSTER_API_KEY";
const PLACEHOLDER_VALUES = new Set([
  "hier_deinen_echten_capmonster_key_eintragen",
  "your_api_key",
  "api_key"
]);

export interface CapMonsterApiKeyHealth {
  provider: "CapMonster";
  configured: boolean;
  success: boolean;
  valid?: boolean;
  checkedAt: string;
  errorCode?: string;
  error?: string;
}

function normalizeKey(value: unknown): string | undefined {
  const key = String(value ?? "").trim().replace(/^['"]|['"]$/g, "");
  if (!key || PLACEHOLDER_VALUES.has(key.toLocaleLowerCase("en-US"))) return undefined;
  return key;
}

export function isCapMonsterApiKeyConfigured(): boolean {
  return Boolean(normalizeKey(process.env[KEY_NAME]));
}

export function loadCapMonsterApiKeyFromEnvFiles(appPath?: string): boolean {
  const existing = normalizeKey(process.env[KEY_NAME]);
  if (existing) {
    process.env[KEY_NAME] = existing;
    return true;
  }

  const roots = [...new Set([process.cwd(), appPath].filter((value): value is string => Boolean(value)))];
  const candidates = roots.flatMap(root => [path.join(root, ".env.txt"), path.join(root, ".env")]);

  for (const filePath of candidates) {
    try {
      if (!fs.existsSync(filePath)) continue;
      const lines = fs.readFileSync(filePath, "utf8").split(/\r?\n/);
      for (const line of lines) {
        const match = line.match(/^\s*CAPMONSTER_API_KEY\s*=\s*(.*?)\s*$/);
        const key = normalizeKey(match?.[1]);
        if (!key) continue;
        process.env[KEY_NAME] = key;
        return true;
      }
    } catch {
      // Status stays unconfigured; never surface file contents or key material.
    }
  }

  delete process.env[KEY_NAME];
  return false;
}

export async function testCapMonsterApiKey(timeoutMs = 8_000): Promise<CapMonsterApiKeyHealth> {
  const checkedAt = new Date().toISOString();
  const clientKey = normalizeKey(process.env[KEY_NAME]);
  if (!clientKey) {
    return {
      provider: "CapMonster",
      configured: false,
      success: false,
      checkedAt,
      error: "CAPMONSTER_API_KEY ist nicht gesetzt."
    };
  }

  const payload = JSON.stringify({ clientKey });

  return new Promise(resolve => {
    const request = https.request({
      protocol: "https:",
      hostname: "api.capmonster.cloud",
      port: 443,
      path: "/getBalance",
      method: "POST",
      headers: {
        "content-type": "application/json",
        "content-length": Buffer.byteLength(payload)
      },
      timeout: Math.max(1_000, Math.min(20_000, Math.floor(timeoutMs)))
    }, response => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", chunk => {
        if (body.length < 16_384) body += String(chunk).slice(0, 16_384 - body.length);
      });
      response.on("end", () => {
        if (!response.statusCode || response.statusCode < 200 || response.statusCode >= 300) {
          resolve({
            provider: "CapMonster",
            configured: true,
            success: false,
            checkedAt,
            error: `CapMonster API antwortet mit HTTP ${response.statusCode ?? "unbekannt"}.`
          });
          return;
        }

        try {
          const parsed = JSON.parse(body) as { errorId?: number; errorCode?: string };
          if (parsed.errorId === 0) {
            resolve({ provider: "CapMonster", configured: true, success: true, valid: true, checkedAt });
            return;
          }

          const errorCode = typeof parsed.errorCode === "string"
            ? parsed.errorCode.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 80)
            : "API_KEY_REJECTED";
          resolve({
            provider: "CapMonster",
            configured: true,
            success: true,
            valid: false,
            checkedAt,
            errorCode,
            error: "CapMonster hat den API-Key abgelehnt."
          });
        } catch {
          resolve({
            provider: "CapMonster",
            configured: true,
            success: false,
            checkedAt,
            error: "CapMonster API lieferte keine verwertbare Antwort."
          });
        }
      });
    });

    request.on("timeout", () => request.destroy(new Error("timeout")));
    request.on("error", error => {
      resolve({
        provider: "CapMonster",
        configured: true,
        success: false,
        checkedAt,
        error: error.message === "timeout" ? "CapMonster API-Check hat das Zeitlimit erreicht." : "CapMonster API-Check konnte nicht abgeschlossen werden."
      });
    });

    request.write(payload);
    request.end();
  });
}
