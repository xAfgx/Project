import type { Task } from "../models";
import type { Page } from "./types";
import type { CommerceShop } from "../commerce/platforms";
import type { ReleaseJourney } from "../commerce/release-discovery/release-journey";
import type { AccountRegistrationInput } from "../commerce/mediamarkt/release-journey";
import { ImapMailbox } from "../mail/imap-mailbox";

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

/**
 * Reads the account-registration payload a task may carry. Returns undefined
 * for every normal task, so callers can use it as a pure no-op gate.
 */
export function accountRegistrationInput(task: Task): AccountRegistrationInput | undefined {
  const data = asRecord(task.config.data?.["accountRegistration"]);
  if (!data) return undefined;
  const email = String(data["email"] ?? "").trim();
  if (!email) return undefined;
  return data as unknown as AccountRegistrationInput;
}

/**
 * Builds an IMAP reader from a mailbox config carried in the task data. The
 * Electron main decrypts the password and puts it here (same pattern as the
 * payment session), because the browser worker has no OS keychain access.
 */
export function mailboxFromTaskData(task: Task, key = "mail"): ImapMailbox | undefined {
  const config = asRecord(task.config.data?.[key]);
  if (!config) return undefined;
  const host = String(config["host"] ?? "").trim();
  const user = String(config["user"] ?? "").trim();
  if (!host || !user) return undefined;
  return new ImapMailbox({
    id: "task-mail",
    name: "task-mail",
    host,
    port: Number(config["port"] ?? 993) || 993,
    secure: config["secure"] !== false,
    user,
    password: String(config["password"] ?? ""),
    mailbox: typeof config["mailbox"] === "string" ? config["mailbox"] : undefined
  });
}

/**
 * Generic account-registration runner. Any executor can call this right after
 * it has a page; it returns:
 *   - undefined when the task is not an account-registration task (no-op),
 *   - true when the journey confirmed the registration,
 *   - false when it failed.
 *
 * Only journeys that implement `registerAccount` participate; everything else
 * keeps its existing behaviour untouched.
 */
export async function runAccountRegistration(options: {
  task: Task;
  page: Page;
  shop: CommerceShop;
  journey: ReleaseJourney;
  signal?: AbortSignal;
}): Promise<boolean | undefined> {
  const input = accountRegistrationInput(options.task);
  if (!input) return undefined;
  process.stderr.write(`[JOURNEY] account-registration start shop=${options.shop.id} email=${input.email}\n`);
  const result = options.journey.registerAccount
    ? await options.journey.registerAccount(options.page, options.shop, input, options.signal)
    : { status: "failed" as const, message: `Journey für ${options.shop.platform} unterstützt keine Registrierung.` };
  options.task.config.data = { ...(options.task.config.data ?? {}), accountRegistrationResult: result };
  process.stderr.write(`[JOURNEY] account-registration ${result.status}: ${result.message}\n`);
  return result.status === "confirmed";
}
