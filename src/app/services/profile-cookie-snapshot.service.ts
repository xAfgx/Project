import { Injectable } from "@angular/core";

export interface CookieSnapshotView {
  id: string;
  profileId: string;
  name: string;
  cookieCount: number;
  createdAt: string;
  updatedAt: string;
}

@Injectable({ providedIn: "root" })
export class ProfileCookieSnapshotService {
  private readonly preview = new Map<string, CookieSnapshotView[]>();

  private get api(): Window["ares"] | undefined {
    return typeof window !== "undefined" ? window.ares : undefined;
  }

  list(profileId: string): Promise<any> {
    if (this.api?.listProfileCookieSnapshots) return this.api.listProfileCookieSnapshots(profileId);
    return Promise.resolve({ success: true, snapshots: this.preview.get(profileId) ?? [], encryptionAvailable: false });
  }

  save(profileId: string, name: string, snapshotId?: string): Promise<any> {
    if (this.api?.saveProfileCookieSnapshot) return this.api.saveProfileCookieSnapshot(profileId, name, snapshotId);
    return Promise.resolve({ success: false, error: "Cookie-Snapshots sind nur in der Electron-App verfügbar." });
  }

  delete(profileId: string, snapshotId: string): Promise<any> {
    if (this.api?.deleteProfileCookieSnapshot) return this.api.deleteProfileCookieSnapshot(profileId, snapshotId);
    const snapshots = this.preview.get(profileId) ?? [];
    this.preview.set(profileId, snapshots.filter(item => item.id !== snapshotId));
    return Promise.resolve({ success: true });
  }
}
