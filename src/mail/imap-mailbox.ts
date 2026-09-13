import { ImapFlow } from "imapflow";
import { simpleParser } from "mailparser";
import type { ImapMailboxConfig, MailConnectionResult, MailMessage, MailSearchOptions } from "./models";

const LINK_PATTERN = /https?:\/\/[^\s"'<>()]+/gi;

function uniqueLinks(values: Array<string | undefined | null>): string[] {
  const seen = new Set<string>();
  const links: string[] = [];
  for (const value of values) {
    if (!value) continue;
    for (const match of value.match(LINK_PATTERN) ?? []) {
      // Strip common trailing punctuation that is not part of the URL.
      const cleaned = match.replace(/[.,;:)\]]+$/, "");
      if (!cleaned || seen.has(cleaned)) continue;
      seen.add(cleaned);
      links.push(cleaned);
    }
  }
  return links;
}

function addressList(value: unknown): string {
  if (Array.isArray(value)) {
    return value
      .map(entry => (entry && typeof entry === "object" ? String((entry as { address?: unknown }).address ?? "") : String(entry ?? "")))
      .filter(Boolean)
      .join(", ");
  }
  return String(value ?? "");
}

/**
 * Small, provider-agnostic IMAP reader used to fetch confirmation mails and
 * verification links for account flows. It only reads: no message is modified.
 */
export class ImapMailbox {
  constructor(private readonly config: ImapMailboxConfig) {}

  private client(): ImapFlow {
    const port = Number.isFinite(this.config.port) && this.config.port > 0 ? this.config.port : 993;
    return new ImapFlow({
      host: this.config.host,
      port,
      secure: this.config.secure !== false,
      auth: { user: this.config.user, pass: this.config.password },
      logger: false
    });
  }

  async testConnection(): Promise<MailConnectionResult> {
    let client: ImapFlow | undefined;
    let lock: { release: () => void } | undefined;
    try {
      client = this.client();
      await client.connect();
      lock = await client.getMailboxLock(this.config.mailbox?.trim() || "INBOX");
      const exists = typeof client.mailbox === "object" && client.mailbox
        ? Number((client.mailbox as { exists?: number }).exists ?? 0)
        : 0;
      return { ok: true, messages: exists };
    } catch (error) {
      return { ok: false, error: error instanceof Error ? error.message : String(error) };
    } finally {
      try { lock?.release(); } catch { /* ignore */ }
      try { await client?.logout(); } catch { /* ignore */ }
    }
  }

  async fetchLatest(options: MailSearchOptions = {}): Promise<MailMessage[]> {
    const limit = Number.isFinite(options.limit) && (options.limit ?? 0) > 0 ? Math.min(50, Math.floor(options.limit as number)) : 10;
    const criteria: Record<string, unknown> = {};
    if (options.since) criteria.since = options.since;
    if (options.unseenOnly) criteria.seen = false;
    if (options.from?.trim()) criteria.from = options.from.trim();
    if (options.to?.trim()) criteria.to = options.to.trim();
    if (options.subjectContains?.trim()) criteria.subject = options.subjectContains.trim();

    let client: ImapFlow | undefined;
    let lock: { release: () => void } | undefined;
    try {
      client = this.client();
      await client.connect();
      lock = await client.getMailboxLock(this.config.mailbox?.trim() || "INBOX");
      const found = await client.search(criteria, { uid: true });
      const uids = Array.isArray(found) ? found.slice(-limit).reverse() : [];
      if (!uids.length) return [];

      const messages: MailMessage[] = [];
      for await (const item of client.fetch(uids, { uid: true, source: true })) {
        const source = (item as { source?: Buffer }).source;
        if (!source) continue;
        const parsed = await simpleParser(source);
        const text = parsed.text ?? "";
        const html = typeof parsed.html === "string" ? parsed.html : "";
        const from = addressList(parsed.from);
        const to = Array.isArray(parsed.to)
          ? parsed.to.map(entry => addressList(entry))
          : [addressList(parsed.to)].filter(Boolean);
        messages.push({
          uid: Number((item as { uid?: number }).uid ?? 0),
          subject: String(parsed.subject ?? ""),
          from,
          to,
          date: parsed.date ? parsed.date.toISOString() : "",
          text,
          html,
          links: uniqueLinks([text, html])
        });
      }

      messages.sort((left, right) => right.date.localeCompare(left.date));
      return messages.slice(0, limit);
    } finally {
      try { lock?.release(); } catch { /* ignore */ }
      try { await client?.logout(); } catch { /* ignore */ }
    }
  }

  /** Polls the mailbox until a matching message arrives or the timeout elapses. */
  async waitForMessage(options: MailSearchOptions, timeoutMs: number, intervalMs = 3_000, signal?: AbortSignal): Promise<MailMessage | undefined> {
    const deadline = Date.now() + Math.max(1_000, timeoutMs);
    do {
      if (signal?.aborted) return undefined;
      const messages = await this.fetchLatest({ ...options, limit: options.limit ?? 5 }).catch(() => [] as MailMessage[]);
      const match = messages.find(message => matches(message, options));
      if (match) return match;
      if (Date.now() >= deadline) return undefined;
      await new Promise(resolve => setTimeout(resolve, Math.max(500, intervalMs)));
    } while (Date.now() < deadline);
    return undefined;
  }

  /**
   * Polls for a matching message and returns the first verification code found
   * (4-8 digits) in the subject or body. Used by registration flows.
   */
  async waitForCode(options: MailSearchOptions, timeoutMs: number, intervalMs = 3_000, signal?: AbortSignal): Promise<string | undefined> {
    const message = await this.waitForMessage(options, timeoutMs, intervalMs, signal);
    if (!message) return undefined;
    return extractVerificationCode(message) ?? undefined;
  }
}

/** Extracts a 4-8 digit verification code from a message subject/body. */
export function extractVerificationCode(message: MailMessage): string | null {
  const sources = [message.subject, message.text, message.html.replace(/<[^>]+>/g, " ")];
  for (const source of sources) {
    const candidates = String(source ?? "").match(/\d{4,8}/g) ?? [];
    if (!candidates.length) continue;
    // Prefer a six-digit code (MediaMarkt sends six digits); otherwise take the
    // first candidate so shops with other lengths still work.
    return candidates.find(candidate => candidate.length === 6) ?? candidates[0];
  }
  return null;
}

function matches(message: MailMessage, options: MailSearchOptions): boolean {
  if (options.from && !message.from.toLowerCase().includes(options.from.toLowerCase())) return false;
  if (options.to && !message.to.join(",").toLowerCase().includes(options.to.toLowerCase())) return false;
  if (options.subjectContains && !message.subject.toLowerCase().includes(options.subjectContains.toLowerCase())) return false;
  return true;
}
