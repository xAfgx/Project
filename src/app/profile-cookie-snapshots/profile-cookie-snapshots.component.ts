import { Component, EventEmitter, Input, OnChanges, Output, SimpleChanges } from "@angular/core";
import { ProfileBrowserService } from "../services/profile-browser.service";
import {
  ProfileCookieSnapshotService,
  type CookieSnapshotView
} from "../services/profile-cookie-snapshot.service";
import {
  getSelectedCookieSnapshot,
  setSelectedCookieSnapshot
} from "../services/profile-cookie-snapshot-selection";

@Component({
  selector: "app-profile-cookie-snapshots",
  templateUrl: "./profile-cookie-snapshots.component.html",
  styleUrls: ["./profile-cookie-snapshots.component.scss"]
})
export class ProfileCookieSnapshotsComponent implements OnChanges {
  @Input() profileId = "";
  @Input() mode: "manage" | "select" = "manage";
  @Input() selectedId = "";
  @Output() selectedIdChange = new EventEmitter<string>();

  snapshots: CookieSnapshotView[] = [];
  snapshotName = "";
  browserOpen = false;
  seleniumBaseBrowserOpen = false;
  seleniumBaseStartUrl = "";
  seleniumBasePid?: number;
  seleniumBaseUserDataDir = "";
  seleniumBaseAppliedSnapshotId = "";
  busy = false;
  error = "";
  info = "";

  constructor(
    private readonly snapshotsApi: ProfileCookieSnapshotService,
    private readonly browserApi: ProfileBrowserService
  ) {}

  ngOnChanges(changes: SimpleChanges): void {
    if (changes["profileId"]) {
      this.selectedId = this.profileId ? getSelectedCookieSnapshot(this.profileId) ?? "" : "";
      void this.refresh();
    }
  }

  async refresh(): Promise<void> {
    this.error = "";
    if (!this.profileId) {
      this.snapshots = [];
      this.browserOpen = false;
      this.seleniumBaseBrowserOpen = false;
      this.seleniumBasePid = undefined;
      this.seleniumBaseUserDataDir = "";
      this.seleniumBaseAppliedSnapshotId = "";
      if (this.selectedId) this.select("");
      return;
    }

    const [snapshotsResult, browserResult, seleniumBaseResult] = await Promise.all([
      this.snapshotsApi.list(this.profileId),
      this.browserApi.getStatus(this.profileId),
      this.browserApi.getSeleniumBaseStatus(this.profileId)
    ]);
    this.snapshots = snapshotsResult?.success && Array.isArray(snapshotsResult.snapshots)
      ? snapshotsResult.snapshots
      : [];
    this.browserOpen = Boolean(browserResult?.success && browserResult.status?.open);
    this.seleniumBaseBrowserOpen = Boolean(seleniumBaseResult?.success && seleniumBaseResult.status?.open);
    this.seleniumBasePid = seleniumBaseResult?.status?.pid;
    this.seleniumBaseUserDataDir = String(seleniumBaseResult?.status?.userDataDir || "");
    this.seleniumBaseAppliedSnapshotId = String(seleniumBaseResult?.status?.appliedSnapshotId || "");
    if (this.selectedId && !this.snapshots.some(item => item.id === this.selectedId)) this.select("");
  }

  async openBrowser(): Promise<void> {
    if (!this.profileId || this.busy) return;
    this.busy = true;
    this.error = "";
    try {
      const result = await this.browserApi.open(this.profileId);
      if (!result?.success) this.error = result?.error || "Profil-Browser konnte nicht geöffnet werden.";
      else {
        this.browserOpen = true;
        this.info = "ARES-Profilbrowser geöffnet. Einloggen/navigieren und danach Snapshot speichern.";
      }
    } finally {
      this.busy = false;
    }
  }

  async closeBrowser(): Promise<void> {
    if (!this.profileId || this.busy) return;
    this.busy = true;
    try {
      const result = await this.browserApi.close(this.profileId);
      if (!result?.success) this.error = result?.error || "Profil-Browser konnte nicht geschlossen werden.";
      else this.browserOpen = false;
    } finally {
      this.busy = false;
    }
  }

  async openSeleniumBaseBrowser(): Promise<void> {
    if (!this.profileId || this.busy) return;
    this.busy = true;
    this.error = "";
    try {
      const result = await this.browserApi.openSeleniumBase(
        this.profileId,
        this.seleniumBaseStartUrl.trim() || undefined,
        this.selectedId || undefined
      );
      if (!result?.success) {
        this.error = result?.error || "SeleniumBase-CDP-Profilbrowser konnte nicht geöffnet werden.";
        return;
      }
      this.seleniumBaseBrowserOpen = true;
      this.seleniumBasePid = result.status?.pid;
      this.seleniumBaseUserDataDir = String(result.status?.userDataDir || "");
      this.seleniumBaseAppliedSnapshotId = String(result.status?.appliedSnapshotId || this.selectedId || "");
      this.info = this.selectedId
        ? "SeleniumBase CDP geöffnet; ausgewählter Snapshot wurde in diese Session geladen."
        : "SeleniumBase CDP geöffnet; der eigene persistente SeleniumBase-Profilstate ist aktiv.";
    } finally {
      this.busy = false;
    }
  }

  async closeSeleniumBaseBrowser(): Promise<void> {
    if (!this.profileId || this.busy) return;
    this.busy = true;
    this.error = "";
    try {
      const result = await this.browserApi.closeSeleniumBase(this.profileId);
      if (!result?.success) {
        this.error = result?.error || "SeleniumBase-CDP-Profilbrowser konnte nicht geschlossen werden.";
        return;
      }
      this.seleniumBaseBrowserOpen = false;
      this.seleniumBasePid = undefined;
      this.seleniumBaseAppliedSnapshotId = "";
      this.info = "SeleniumBase CDP sauber beendet; Profilstate wurde über sb.quit() geschlossen.";
    } finally {
      this.busy = false;
    }
  }

  async applySelectedToSeleniumBase(): Promise<void> {
    if (!this.profileId || !this.selectedId || !this.seleniumBaseBrowserOpen || this.busy) return;
    this.busy = true;
    this.error = "";
    try {
      const result = await this.browserApi.applySeleniumBaseSnapshot(this.profileId, this.selectedId);
      if (!result?.success) {
        this.error = result?.error || "Cookie-Snapshot konnte nicht in die SeleniumBase-Session geladen werden.";
        return;
      }
      this.seleniumBaseAppliedSnapshotId = this.selectedId;
      this.info = `${result.count ?? 0} Cookies in die laufende SeleniumBase-CDP-Session geladen.`;
    } finally {
      this.busy = false;
    }
  }

  async saveSnapshot(): Promise<void> {
    const name = this.snapshotName.trim();
    if (!this.profileId || !name || this.busy) {
      if (!name) this.error = "Bitte einen Snapshot-Namen eingeben.";
      return;
    }
    if (!this.browserOpen) {
      this.error = "ARES-Profilbrowser zuerst öffnen. Gespeichert werden nur die Cookies der aktuell geöffneten Session.";
      return;
    }

    this.busy = true;
    this.error = "";
    try {
      const result = await this.snapshotsApi.save(this.profileId, name);
      if (!result?.success) {
        this.error = result?.error || "Cookie-Snapshot konnte nicht gespeichert werden.";
        return;
      }
      this.snapshotName = "";
      this.info = `${result.snapshot?.cookieCount ?? 0} Browser-Cookies verschlüsselt gespeichert.`;
      await this.refresh();
      if (result.snapshot?.id) this.select(result.snapshot.id);
    } finally {
      this.busy = false;
    }
  }

  async saveSeleniumBaseSnapshot(): Promise<void> {
    const name = this.snapshotName.trim();
    if (!this.profileId || !name || this.busy) {
      if (!name) this.error = "Bitte einen Snapshot-Namen eingeben.";
      return;
    }
    if (!this.seleniumBaseBrowserOpen) {
      this.error = "SeleniumBase-CDP-Profilbrowser zuerst öffnen.";
      return;
    }

    this.busy = true;
    this.error = "";
    try {
      const result = await this.browserApi.saveSeleniumBaseSnapshot(this.profileId, name);
      if (!result?.success) {
        this.error = result?.error || "SeleniumBase-Cookies konnten nicht gespeichert werden.";
        return;
      }
      this.snapshotName = "";
      this.info = `${result.snapshot?.cookieCount ?? 0} SeleniumBase-CDP-Cookies verschlüsselt gespeichert.`;
      await this.refresh();
      if (result.snapshot?.id) this.select(result.snapshot.id);
    } finally {
      this.busy = false;
    }
  }

  async deleteSnapshot(snapshot: CookieSnapshotView): Promise<void> {
    if (!this.profileId || this.busy) return;
    this.busy = true;
    this.error = "";
    try {
      const result = await this.snapshotsApi.delete(this.profileId, snapshot.id);
      if (!result?.success) this.error = result?.error || "Snapshot konnte nicht gelöscht werden.";
      else {
        if (this.selectedId === snapshot.id) this.select("");
        if (this.seleniumBaseAppliedSnapshotId === snapshot.id) this.seleniumBaseAppliedSnapshotId = "";
        await this.refresh();
      }
    } finally {
      this.busy = false;
    }
  }

  select(id: string): void {
    this.selectedId = id;
    if (this.profileId) setSelectedCookieSnapshot(this.profileId, id);
    this.selectedIdChange.emit(id);
  }

  formatDate(value: string): string {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString("de-DE");
  }
}
