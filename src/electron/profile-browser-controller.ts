import { ipcMain } from "electron";
import * as path from "path";
import type { AresProfile } from "../profiles/models";
import type { AresProxy } from "../proxies/models";
import type { ProfileCookieSnapshotCookie } from "../cookies/profile-cookie-snapshot-vault";
import { removeProfileUserDataDir } from "../browser-worker/profile-session-manager";
import { registerProfilePaymentIpc } from "./profile-payment-controller";
import { registerProfileCookieSnapshotIpc } from "./profile-cookie-snapshot-controller";
import {
  SeleniumBaseProfileBrowserController,
  type SeleniumBaseProfileBrowserStatus
} from "./seleniumbase-profile-browser-controller";
import { SeleniumBaseProductMonitorBrowserAdapter } from "./seleniumbase-product-monitor-browser-adapter";
import { SeleniumBaseVisionRuntime } from "./seleniumbase-vision-runtime";
import { setDefaultBrowserProductFallback } from "../monitor/browser-product-fallback-registry";

export type ProfileBrowserStatus = SeleniumBaseProfileBrowserStatus;

export interface ProfileBrowserOpenOptions {
  engine?: "seleniumbase-cdp";
  startUrl?: string;
  cookieSnapshotId?: string;
}

export class ProfileBrowserController {
  private readonly seleniumBase: SeleniumBaseProfileBrowserController;
  private readonly productMonitorBrowser: SeleniumBaseProductMonitorBrowserAdapter;
  private readonly visionRuntime = new SeleniumBaseVisionRuntime();

  constructor(
    private readonly profileRoot: string,
    getProxy: (proxyId: string) => AresProxy | undefined
  ) {
    const userDataRoot = path.dirname(profileRoot);
    registerProfilePaymentIpc(userDataRoot);
    registerProfileCookieSnapshotIpc(userDataRoot, this);
    this.seleniumBase = new SeleniumBaseProfileBrowserController(profileRoot, getProxy);
    this.productMonitorBrowser = new SeleniumBaseProductMonitorBrowserAdapter(profileRoot, getProxy);
    setDefaultBrowserProductFallback(this.productMonitorBrowser);
    this.registerSeleniumBaseIpc();
  }

  async open(
    profile: AresProfile,
    startUrlOrOptions?: string | ProfileBrowserOpenOptions
  ): Promise<ProfileBrowserStatus> {
    const options = typeof startUrlOrOptions === "string"
      ? { startUrl: startUrlOrOptions }
      : (startUrlOrOptions ?? {});

    // The manual browser must inherit the shared vision service URL/token at
    // spawn time. Waiting here is only for the lightweight loopback listener;
    // SigLIP2 itself preloads asynchronously inside that persistent service.
    await this.visionRuntime.ensureSharedService().catch(() => undefined);
    void this.visionRuntime.prepare().catch(() => undefined);
    return this.seleniumBase.open(profile, options.startUrl, options.cookieSnapshotId);
  }

  captureCookies(profileId: string): Promise<ProfileCookieSnapshotCookie[]> {
    return this.seleniumBase.captureCookies(profileId);
  }

  close(profileId: string): Promise<ProfileBrowserStatus> {
    return this.seleniumBase.close(profileId);
  }

  async resetSession(profileId: string): Promise<ProfileBrowserStatus> {
    const id = String(profileId ?? "").trim();
    if (!id) throw new Error("Profil-ID fehlt.");
    await this.seleniumBase.close(id);
    removeProfileUserDataDir(id, this.profileRoot);
    return this.seleniumBase.status(id);
  }

  status(profileId: string): ProfileBrowserStatus {
    return this.seleniumBase.status(profileId);
  }

  isOpen(profileId: string): boolean {
    return this.seleniumBase.isOpen(profileId);
  }

  async closeAll(): Promise<void> {
    await Promise.all([
      this.seleniumBase.closeAll(),
      this.productMonitorBrowser.close()
    ]);
    await this.visionRuntime.shutdown().catch(() => undefined);
  }

  private registerSeleniumBaseIpc(): void {
    ipcMain.handle("get-seleniumbase-profile-browser-status", (_event, profileId: string) => {
      try {
        return { success: true, status: this.seleniumBase.status(profileId) };
      } catch (error) {
        return { success: false, error: error instanceof Error ? error.message : String(error) };
      }
    });

    ipcMain.handle("get-seleniumbase-vision-status", async () => {
      try {
        return { success: true, status: await this.visionRuntime.status() };
      } catch (error) {
        return { success: false, error: error instanceof Error ? error.message : String(error) };
      }
    });

    ipcMain.handle("prepare-seleniumbase-vision", async () => {
      try {
        return { success: true, status: await this.visionRuntime.prepare() };
      } catch (error) {
        return { success: false, error: error instanceof Error ? error.message : String(error) };
      }
    });

    ipcMain.handle("close-seleniumbase-profile-browser", async (_event, profileId: string) => {
      try {
        return { success: true, status: await this.seleniumBase.close(profileId) };
      } catch (error) {
        return { success: false, error: error instanceof Error ? error.message : String(error) };
      }
    });

    ipcMain.handle("reset-profile-browser-session", async (_event, profileId: string) => {
      try {
        return { success: true, status: await this.resetSession(profileId) };
      } catch (error) {
        return { success: false, error: error instanceof Error ? error.message : String(error) };
      }
    });

    ipcMain.handle("apply-seleniumbase-cookie-snapshot", async (_event, profileId: string, snapshotId: string) => {
      try {
        return { success: true, ...(await this.seleniumBase.applySnapshot(profileId, snapshotId)) };
      } catch (error) {
        return { success: false, error: error instanceof Error ? error.message : String(error) };
      }
    });

    ipcMain.handle("save-seleniumbase-profile-cookie-snapshot", async (
      _event,
      profileId: string,
      name: string,
      snapshotId?: string
    ) => {
      try {
        const snapshot = await this.seleniumBase.saveSnapshot(profileId, name, snapshotId);
        return { success: true, snapshot };
      } catch (error) {
        return { success: false, error: error instanceof Error ? error.message : String(error) };
      }
    });
  }
}
