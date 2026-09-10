import { Injectable } from "@angular/core";
import { setSelectedCookieSnapshot } from "./profile-cookie-snapshot-selection";

export interface ProfileBrowserStatusView {
  profileId: string;
  open: boolean;
  pid?: number;
  userDataDir?: string;
  startedAt?: string;
}

export interface SeleniumBaseProfileBrowserStatusView extends ProfileBrowserStatusView {
  engine: "seleniumbase-cdp";
  appliedSnapshotId?: string;
}

@Injectable({ providedIn: "root" })
export class ProfileBrowserService {
  private readonly previewOpen = new Set<string>();
  private readonly seleniumBasePreviewOpen = new Set<string>();

  private get api(): Window["ares"] | undefined {
    return typeof window !== "undefined" ? window.ares : undefined;
  }

  getStatus(profileId: string): Promise<any> {
    if (this.api?.getProfileBrowserStatus) return this.api.getProfileBrowserStatus(profileId);
    return Promise.resolve({
      success: true,
      status: { profileId, open: this.previewOpen.has(profileId) }
    });
  }

  open(profileId: string, startUrl?: string): Promise<any> {
    if (this.api?.openProfileBrowser) return this.api.openProfileBrowser(profileId, startUrl);
    this.previewOpen.add(profileId);
    return Promise.resolve({
      success: true,
      status: { profileId, open: true, startedAt: new Date().toISOString() }
    });
  }

  close(profileId: string): Promise<any> {
    if (this.api?.closeProfileBrowser) return this.api.closeProfileBrowser(profileId);
    this.previewOpen.delete(profileId);
    return Promise.resolve({
      success: true,
      status: { profileId, open: false }
    });
  }

  async resetSession(profileId: string): Promise<any> {
    const id = String(profileId ?? "").trim();
    if (!id) return { success: false, error: "Profil-ID fehlt." };
    let result: any;
    if (this.api?.resetProfileBrowserSession) {
      result = await this.api.resetProfileBrowserSession(id);
    } else {
      this.previewOpen.delete(id);
      this.seleniumBasePreviewOpen.delete(id);
      result = { success: true, status: { engine: "seleniumbase-cdp", profileId: id, open: false } };
    }
    if (result?.success) setSelectedCookieSnapshot(id, "");
    return result;
  }

  getSeleniumBaseStatus(profileId: string): Promise<any> {
    if (this.api?.getSeleniumBaseProfileBrowserStatus) return this.api.getSeleniumBaseProfileBrowserStatus(profileId);
    return Promise.resolve({
      success: true,
      status: {
        engine: "seleniumbase-cdp",
        profileId,
        open: this.seleniumBasePreviewOpen.has(profileId)
      }
    });
  }

  openSeleniumBase(profileId: string, startUrl?: string, cookieSnapshotId?: string): Promise<any> {
    if (this.api?.openSeleniumBaseProfileBrowser) {
      return this.api.openSeleniumBaseProfileBrowser(profileId, startUrl, cookieSnapshotId);
    }
    this.seleniumBasePreviewOpen.add(profileId);
    return Promise.resolve({
      success: true,
      status: {
        engine: "seleniumbase-cdp",
        profileId,
        open: true,
        appliedSnapshotId: cookieSnapshotId,
        startedAt: new Date().toISOString()
      }
    });
  }

  closeSeleniumBase(profileId: string): Promise<any> {
    if (this.api?.closeSeleniumBaseProfileBrowser) return this.api.closeSeleniumBaseProfileBrowser(profileId);
    this.seleniumBasePreviewOpen.delete(profileId);
    return Promise.resolve({
      success: true,
      status: { engine: "seleniumbase-cdp", profileId, open: false }
    });
  }

  applySeleniumBaseSnapshot(profileId: string, snapshotId: string): Promise<any> {
    if (this.api?.applySeleniumBaseCookieSnapshot) return this.api.applySeleniumBaseCookieSnapshot(profileId, snapshotId);
    return Promise.resolve({ success: true, snapshotId, count: 0 });
  }

  saveSeleniumBaseSnapshot(profileId: string, name: string, snapshotId?: string): Promise<any> {
    if (this.api?.saveSeleniumBaseProfileCookieSnapshot) {
      return this.api.saveSeleniumBaseProfileCookieSnapshot(profileId, name, snapshotId);
    }
    return Promise.resolve({ success: false, error: "Cookie-Snapshots sind nur in Electron verfügbar." });
  }
}
