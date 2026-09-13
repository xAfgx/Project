import { ipcMain, safeStorage } from "electron";
import * as path from "path";
import { AccountStore, type AccountSecretCrypto } from "../accounts/account-store";
import type { CreateAccountInput } from "../accounts/models";

function electronAccountCrypto(): AccountSecretCrypto {
  return {
    isEncryptionAvailable: () => safeStorage.isEncryptionAvailable(),
    encryptString: value => safeStorage.encryptString(value),
    decryptString: value => safeStorage.decryptString(value)
  };
}

export function registerAccountIpc(userDataRoot: string): AccountStore {
  const store = new AccountStore(path.join(userDataRoot, "accounts.json"), electronAccountCrypto());

  ipcMain.handle("get-accounts", () => {
    try {
      return { success: true, accounts: store.list(), encryptionAvailable: store.isEncryptionAvailable() };
    } catch (error) {
      return { success: false, accounts: [], encryptionAvailable: false, error: error instanceof Error ? error.message : String(error) };
    }
  });

  ipcMain.handle("create-account", (_event, input: CreateAccountInput) => {
    try {
      const record = store.create(input);
      return { success: true, account: record, accounts: store.list() };
    } catch (error) {
      return { success: false, error: error instanceof Error ? error.message : String(error) };
    }
  });

  ipcMain.handle("update-account-status", (_event, accountId: string, status: string, message?: string) => {
    try {
      const account = store.updateStatus(accountId, status as never, message);
      return { success: Boolean(account), account, accounts: store.list() };
    } catch (error) {
      return { success: false, error: error instanceof Error ? error.message : String(error) };
    }
  });

  ipcMain.handle("reveal-account", (_event, accountId: string) => {
    try {
      const account = store.get(accountId);
      if (!account) return { success: false, error: "Account wurde nicht gefunden." };
      return { success: true, email: account.email, password: account.password };
    } catch (error) {
      return { success: false, error: error instanceof Error ? error.message : String(error) };
    }
  });

  ipcMain.handle("delete-account", (_event, accountId: string) => {
    try {
      return { success: store.delete(accountId), accounts: store.list() };
    } catch (error) {
      return { success: false, error: error instanceof Error ? error.message : String(error) };
    }
  });

  return store;
}
