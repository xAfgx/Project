const selectedByProfile = new Map<string, string>();

export function setSelectedCookieSnapshot(profileId: string, snapshotId: string): void {
  const profile = String(profileId ?? "").trim();
  if (!profile) return;
  const snapshot = String(snapshotId ?? "").trim();
  if (snapshot) selectedByProfile.set(profile, snapshot);
  else selectedByProfile.delete(profile);
}

export function getSelectedCookieSnapshot(profileId: string): string | undefined {
  return selectedByProfile.get(String(profileId ?? "").trim());
}
