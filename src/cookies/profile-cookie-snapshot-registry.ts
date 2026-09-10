import type {
  ProfileCookieSnapshotCookie,
  ProfileCookieSnapshotSummary,
  ProfileCookieSnapshotVault
} from "./profile-cookie-snapshot-vault";

let registeredVault: ProfileCookieSnapshotVault | undefined;

export function registerProfileCookieSnapshotVault(vault: ProfileCookieSnapshotVault): void {
  registeredVault = vault;
}

export function readRegisteredProfileCookieSnapshot(
  profileId: string,
  snapshotId: string
): ProfileCookieSnapshotCookie[] | undefined {
  if (!registeredVault) return undefined;
  return registeredVault.read(profileId, snapshotId);
}

export function saveRegisteredProfileCookieSnapshot(
  profileId: string,
  name: string,
  cookies: ProfileCookieSnapshotCookie[],
  snapshotId?: string
): ProfileCookieSnapshotSummary {
  if (!registeredVault) throw new Error("Cookie-Snapshot Vault ist noch nicht registriert.");
  return registeredVault.save(profileId, name, cookies, snapshotId);
}

export function deleteRegisteredProfileCookieSnapshots(profileId: string): number {
  return registeredVault?.deleteProfile(profileId) ?? 0;
}
