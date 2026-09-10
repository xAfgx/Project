import { ipcMain, safeStorage } from "electron";
import * as path from "path";
import {
  ProfileCookieSnapshotVault,
  type CookieSnapshotCrypto
} from "../cookies/profile-cookie-snapshot-vault";
import { registerProfileCookieSnapshotVault } from "../cookies/profile-cookie-snapshot-registry";
import type { ProfileBrowserController } from "./profile-browser-controller";

function electronCookieCrypto(): CookieSnapshotCrypto {
  return {
    isEncryptionAvailable: () => safeStorage.isEncryptionAvailable(),
    encryptString: value => safeStorage.encryptString(value),
    decryptString: value => safeStorage.decryptString(value)
  };
}

export function registerProfileCookieSnapshotIpc(
  userDataRoot: string,
  profileBrowserController: ProfileBrowserController
): ProfileCookieSnapshotVault {
  const vault = new ProfileCookieSnapshotVault(
    path.join(userDataRoot, "cookie-snapshots.json"),
    electronCookieCrypto()
  );
  registerProfileCookieSnapshotVault(vault);

  ipcMain.handle("list-profile-cookie-snapshots", (_event, profileId: string) => {
    try {
      return { success: true, snapshots: vault.list(profileId), encryptionAvailable: vault.isEncryptionAvailable() };
    } catch (error) {
      return { success: false, snapshots: [], encryptionAvailable: vault.isEncryptionAvailable(), error: error instanceof Error ? error.message : String(error) };
    }
  });

  ipcMain.handle("save-profile-cookie-snapshot", async (_event, profileId: string, name: string, snapshotId?: string) => {
    try {
      const cookies = await profileBrowserController.captureCookies(profileId);
      const snapshot = vault.save(profileId, name, cookies, snapshotId);
      return { success: true, snapshot };
    } catch (error) {
      return { success: false, error: error instanceof Error ? error.message : String(error) };
    }
  });

  ipcMain.handle("delete-profile-cookie-snapshot", (_event, profileId: string, snapshotId: string) => {
    try { return { success: vault.delete(profileId, snapshotId) }; }
    catch (error) { return { success: false, error: error instanceof Error ? error.message : String(error) }; }
  });

  return vault;
}
