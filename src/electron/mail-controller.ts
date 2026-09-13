import { ipcMain, safeStorage } from "electron";
import * as path from "path";
import { ImapMailboxRepository, type MailSecretCrypto } from "../mail/imap-repository";
import { ImapMailbox } from "../mail/imap-mailbox";
import type { ImapMailboxConfig, MailSearchOptions } from "../mail/models";

function electronMailCrypto(): MailSecretCrypto {
  return {
    isEncryptionAvailable: () => safeStorage.isEncryptionAvailable(),
    encryptString: value => safeStorage.encryptString(value),
    decryptString: value => safeStorage.decryptString(value)
  };
}

function resolveConfig(repository: ImapMailboxRepository, input: ImapMailboxConfig | string): ImapMailboxConfig {
  if (typeof input === "string") {
    const config = repository.get(input);
    if (!config) throw new Error("IMAP-Postfach wurde nicht gefunden.");
    return config;
  }
  // Inline test (before saving): use the provided values directly.
  return input;
}

export function registerMailIpc(userDataRoot: string): ImapMailboxRepository {
  const repository = new ImapMailboxRepository(path.join(userDataRoot, "mailboxes.json"), electronMailCrypto());

  ipcMain.handle("get-mailboxes", () => {
    try {
      return { success: true, mailboxes: repository.list(), encryptionAvailable: repository.isEncryptionAvailable() };
    } catch (error) {
      return { success: false, mailboxes: [], encryptionAvailable: false, error: error instanceof Error ? error.message : String(error) };
    }
  });

  ipcMain.handle("save-mailbox", (_event, input: Partial<ImapMailboxConfig> & { id?: string }) => {
    try {
      return { success: true, mailbox: repository.save(input), mailboxes: repository.list() };
    } catch (error) {
      return { success: false, error: error instanceof Error ? error.message : String(error) };
    }
  });

  ipcMain.handle("delete-mailbox", (_event, mailboxId: string) => {
    try {
      return { success: repository.delete(mailboxId), mailboxes: repository.list() };
    } catch (error) {
      return { success: false, error: error instanceof Error ? error.message : String(error) };
    }
  });

  ipcMain.handle("test-mailbox", async (_event, input: ImapMailboxConfig | string) => {
    try {
      const config = resolveConfig(repository, input);
      const result = await new ImapMailbox(config).testConnection();
      return { success: result.ok, result, error: result.error };
    } catch (error) {
      return { success: false, error: error instanceof Error ? error.message : String(error) };
    }
  });

  ipcMain.handle("fetch-mailbox-messages", async (_event, mailboxId: string, options: MailSearchOptions = {}) => {
    try {
      const config = repository.get(mailboxId);
      if (!config) throw new Error("IMAP-Postfach wurde nicht gefunden.");
      const messages = await new ImapMailbox(config).fetchLatest({ ...options, limit: options.limit ?? 5 });
      return { success: true, messages };
    } catch (error) {
      return { success: false, messages: [], error: error instanceof Error ? error.message : String(error) };
    }
  });

  // Internal use: the registration task needs the decrypted config. Never
  // rendered in the UI.
  ipcMain.handle("get-mailbox-secret", (_event, mailboxId: string) => {
    try {
      const config = repository.get(mailboxId);
      if (!config) return { success: false, error: "IMAP-Postfach wurde nicht gefunden." };
      return { success: true, config };
    } catch (error) {
      return { success: false, error: error instanceof Error ? error.message : String(error) };
    }
  });

  return repository;
}
