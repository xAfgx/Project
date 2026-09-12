import { ipcMain, safeStorage } from "electron";
import * as path from "path";
import {
  ProfilePaymentVault,
  type PaymentVaultCrypto,
  type ProfilePaymentCardDraft
} from "../payments/profile-payment-vault";

let registeredVault: ProfilePaymentVault | undefined;

function electronVaultCrypto(): PaymentVaultCrypto {
  return {
    isEncryptionAvailable: () => safeStorage.isEncryptionAvailable(),
    encryptString: value => safeStorage.encryptString(value),
    decryptString: value => safeStorage.decryptString(value)
  };
}

/** Register renderer-safe payment-vault IPC once and return the shared vault instance. */
export function registerProfilePaymentIpc(userDataRoot: string): ProfilePaymentVault {
  if (registeredVault) return registeredVault;

  const vault = new ProfilePaymentVault(
    path.join(userDataRoot, "payment-vault.json"),
    electronVaultCrypto()
  );
  registeredVault = vault;

  ipcMain.handle("get-profile-payment", (_event, profileId: string) => {
    try {
      return {
        success: true,
        payment: vault.getView(profileId),
        encryptionAvailable: vault.isEncryptionAvailable()
      };
    } catch (error) {
      return {
        success: false,
        payment: { configured: false },
        encryptionAvailable: vault.isEncryptionAvailable(),
        error: error instanceof Error ? error.message : String(error)
      };
    }
  });

  ipcMain.handle("save-profile-payment", (_event, profileId: string, input: ProfilePaymentCardDraft) => {
    try {
      const payment = vault.save(profileId, input ?? {});
      return {
        success: true,
        payment,
        encryptionAvailable: vault.isEncryptionAvailable()
      };
    } catch (error) {
      return {
        success: false,
        encryptionAvailable: vault.isEncryptionAvailable(),
        error: error instanceof Error ? error.message : String(error)
      };
    }
  });

  ipcMain.handle("delete-profile-payment", (_event, profileId: string) => {
    try {
      vault.delete(profileId);
      return { success: true };
    } catch (error) {
      return { success: false, error: error instanceof Error ? error.message : String(error) };
    }
  });

  ipcMain.handle("list-payment-cards", () => {
    try {
      return {
        success: true,
        cards: vault.listCards(),
        encryptionAvailable: vault.isEncryptionAvailable()
      };
    } catch (error) {
      return { success: false, cards: [], error: error instanceof Error ? error.message : String(error) };
    }
  });

  ipcMain.handle("save-payment-card", (_event, cardId: string, input: ProfilePaymentCardDraft, label?: string) => {
    try {
      const view = vault.saveCard(cardId, input ?? {}, label);
      return { success: true, card: view, encryptionAvailable: vault.isEncryptionAvailable() };
    } catch (error) {
      return { success: false, encryptionAvailable: vault.isEncryptionAvailable(), error: error instanceof Error ? error.message : String(error) };
    }
  });

  ipcMain.handle("delete-payment-card", (_event, cardId: string) => {
    try {
      return { success: true, deleted: vault.deleteCard(cardId) };
    } catch (error) {
      return { success: false, error: error instanceof Error ? error.message : String(error) };
    }
  });

  ipcMain.handle("get-profile-payment-card", (_event, profileId: string) => {
    try {
      return { success: true, cardId: vault.assignedCardId(profileId) };
    } catch (error) {
      return { success: false, cardId: "", error: error instanceof Error ? error.message : String(error) };
    }
  });

  ipcMain.handle("assign-profile-payment-card", (_event, profileId: string, cardId: string | null) => {
    try {
      vault.assignCard(profileId, cardId);
      return { success: true, cardId: vault.assignedCardId(profileId) };
    } catch (error) {
      return { success: false, error: error instanceof Error ? error.message : String(error) };
    }
  });

  return vault;
}
