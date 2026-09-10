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

  return vault;
}
