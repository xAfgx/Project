import Database from "better-sqlite3";

const SENSITIVE_KEY = /(api[-_]?key|authorization|cookie|password|secret|token)/i;
const SENSITIVE_VALUE = /\b(api[-_]?key|authorization|cookie|password|secret|token|card[-_]?number|pan|cvc|cvv|security[-_]?code)\s*[:=]\s*([^\s,;]+)/gi;
const PAN_LIKE_VALUE = /\b(?:\d[ -]?){11,18}\d\b/g;

function isDropSensitiveKey(key: string): boolean {
  const normalized = key.replace(/[^a-z0-9]/gi, "").toLowerCase();
  return normalized === "paymentsession"
    || normalized.includes("cardnumber")
    || normalized.includes("securitycode")
    || normalized === "pan"
    || normalized.startsWith("cvc")
    || normalized.endsWith("cvc")
    || normalized.startsWith("cvv")
    || normalized.endsWith("cvv");
}

export function sanitizePersistedValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(item => sanitizePersistedValue(item));
  if (!value || typeof value !== "object") return value;

  const result: Record<string, unknown> = {};
  for (const [key, nested] of Object.entries(value as Record<string, unknown>)) {
    if (isDropSensitiveKey(key)) continue;
    result[key] = SENSITIVE_KEY.test(key) ? "[REDACTED]" : sanitizePersistedValue(nested);
  }
  return result;
}

export function sanitizePersistedMessage(message: string): string {
  const labeled = message.replace(SENSITIVE_VALUE, (_match, label: string) => `${label}=[REDACTED]`);
  return labeled.replace(PAN_LIKE_VALUE, candidate => {
    const digits = candidate.replace(/\D/g, "");
    return digits.length >= 12 && digits.length <= 19 ? "[REDACTED_PAN]" : candidate;
  });
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : String(value ?? "");
}

function asNumber(value: unknown): number {
  return typeof value === "number" ? value : Number(value ?? 0);
}

/**
 * One-way cleanup for databases created before payment sessions became strictly ephemeral.
 * Plaintext payment data is removed/redacted in place and is never migrated into the vault.
 */
export function scrubLegacySensitiveData(db: Database): void {
  const scrub = db.transaction(() => {
    const taskRows = db.prepare("SELECT id, config_json, last_error FROM tasks").all() as Record<string, unknown>[];
    const updateTask = db.prepare("UPDATE tasks SET config_json = ?, last_error = ? WHERE id = ?");
    for (const row of taskRows) {
      const id = asString(row["id"]);
      const rawConfig = asString(row["config_json"]);
      const rawLastError = row["last_error"] == null ? null : asString(row["last_error"]);
      try {
        const sanitizedConfig = JSON.stringify(sanitizePersistedValue(JSON.parse(rawConfig)));
        const sanitizedLastError = rawLastError == null ? null : sanitizePersistedMessage(rawLastError);
        if (sanitizedConfig !== rawConfig || sanitizedLastError !== rawLastError) {
          updateTask.run(sanitizedConfig, sanitizedLastError, id);
        }
      } catch {
        // Malformed legacy JSON remains untouched so normal read validation can surface it.
      }
    }

    const logRows = db.prepare("SELECT id, message FROM task_logs").all() as Record<string, unknown>[];
    const updateLog = db.prepare("UPDATE task_logs SET message = ? WHERE id = ?");
    for (const row of logRows) {
      const id = asNumber(row["id"]);
      const rawMessage = asString(row["message"]);
      const sanitizedMessage = sanitizePersistedMessage(rawMessage);
      if (sanitizedMessage !== rawMessage) updateLog.run(sanitizedMessage, id);
    }

    const eventRows = db.prepare("SELECT id, event_json FROM product_monitor_events").all() as Record<string, unknown>[];
    const updateEvent = db.prepare("UPDATE product_monitor_events SET event_json = ? WHERE id = ?");
    for (const row of eventRows) {
      const id = asNumber(row["id"]);
      const rawJson = asString(row["event_json"]);
      try {
        const sanitizedJson = JSON.stringify(sanitizePersistedValue(JSON.parse(rawJson)));
        if (sanitizedJson !== rawJson) updateEvent.run(sanitizedJson, id);
      } catch {
        // Keep malformed legacy event JSON untouched for normal read validation.
      }
    }
  });
  scrub();
}
