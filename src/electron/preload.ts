import { contextBridge, ipcRenderer } from "electron";

const taskStatusListeners = new Map<Function, (_event: Electron.IpcRendererEvent, payload: unknown) => void>();
const monitorListeners = new Map<Function, (_event: Electron.IpcRendererEvent, payload: unknown) => void>();
const liveLogListeners = new Map<Function, (_event: Electron.IpcRendererEvent, payload: unknown) => void>();

const api = {
  getProfiles: () => ipcRenderer.invoke("get-profiles"),
  saveProfile: (profile: unknown) => ipcRenderer.invoke("save-profile", profile),
  deleteProfile: (profileId: string) => ipcRenderer.invoke("delete-profile", profileId),
  getProfilePayment: (profileId: string) => ipcRenderer.invoke("get-profile-payment", profileId),
  saveProfilePayment: (profileId: string, payment: unknown) => ipcRenderer.invoke("save-profile-payment", profileId, payment),
  deleteProfilePayment: (profileId: string) => ipcRenderer.invoke("delete-profile-payment", profileId),
  listPaymentCards: () => ipcRenderer.invoke("list-payment-cards"),
  savePaymentCard: (cardId: string, payment: unknown, label?: string) => ipcRenderer.invoke("save-payment-card", cardId, payment, label),
  deletePaymentCard: (cardId: string) => ipcRenderer.invoke("delete-payment-card", cardId),
  getProfilePaymentCard: (profileId: string) => ipcRenderer.invoke("get-profile-payment-card", profileId),
  assignProfilePaymentCard: (profileId: string, cardId: string | null) => ipcRenderer.invoke("assign-profile-payment-card", profileId, cardId),
  getProfileBrowserStatus: (profileId: string) => ipcRenderer.invoke("get-profile-browser-status", profileId),
  openProfileBrowser: (profileId: string, startUrl?: string) => ipcRenderer.invoke("open-profile-browser", profileId, startUrl),
  closeProfileBrowser: (profileId: string) => ipcRenderer.invoke("close-profile-browser", profileId),
  resetProfileBrowserSession: (profileId: string) => ipcRenderer.invoke("reset-profile-browser-session", profileId),
  getSeleniumBaseProfileBrowserStatus: (profileId: string) => ipcRenderer.invoke("get-seleniumbase-profile-browser-status", profileId),
  getSeleniumBaseVisionStatus: () => ipcRenderer.invoke("get-seleniumbase-vision-status"),
  prepareSeleniumBaseVision: () => ipcRenderer.invoke("prepare-seleniumbase-vision"),
  openSeleniumBaseProfileBrowser: (profileId: string, startUrl?: string, cookieSnapshotId?: string) => ipcRenderer.invoke(
    "open-profile-browser",
    profileId,
    { engine: "seleniumbase-cdp", startUrl, cookieSnapshotId }
  ),
  closeSeleniumBaseProfileBrowser: (profileId: string) => ipcRenderer.invoke("close-seleniumbase-profile-browser", profileId),
  applySeleniumBaseCookieSnapshot: (profileId: string, snapshotId: string) => ipcRenderer.invoke("apply-seleniumbase-cookie-snapshot", profileId, snapshotId),
  saveSeleniumBaseProfileCookieSnapshot: (profileId: string, name: string, snapshotId?: string) => ipcRenderer.invoke("save-seleniumbase-profile-cookie-snapshot", profileId, name, snapshotId),
  listProfileCookieSnapshots: (profileId: string) => ipcRenderer.invoke("list-profile-cookie-snapshots", profileId),
  saveProfileCookieSnapshot: (profileId: string, name: string, snapshotId?: string) => ipcRenderer.invoke("save-profile-cookie-snapshot", profileId, name, snapshotId),
  deleteProfileCookieSnapshot: (profileId: string, snapshotId: string) => ipcRenderer.invoke("delete-profile-cookie-snapshot", profileId, snapshotId),
  getProxies: () => ipcRenderer.invoke("get-proxies"),
  saveProxy: (proxy: unknown) => ipcRenderer.invoke("save-proxy", proxy),
  testProxy: (proxyId: string) => ipcRenderer.invoke("test-proxy", proxyId),
  testAllProxies: () => ipcRenderer.invoke("test-all-proxies"),
  deleteProxy: (proxyId: string) => ipcRenderer.invoke("delete-proxy", proxyId),
  getMailboxes: () => ipcRenderer.invoke("get-mailboxes"),
  saveMailbox: (mailbox: unknown) => ipcRenderer.invoke("save-mailbox", mailbox),
  deleteMailbox: (mailboxId: string) => ipcRenderer.invoke("delete-mailbox", mailboxId),
  testMailbox: (mailbox: unknown) => ipcRenderer.invoke("test-mailbox", mailbox),
  fetchMailboxMessages: (mailboxId: string, options?: unknown) => ipcRenderer.invoke("fetch-mailbox-messages", mailboxId, options),
  getMailboxSecret: (mailboxId: string) => ipcRenderer.invoke("get-mailbox-secret", mailboxId),
  getAccounts: () => ipcRenderer.invoke("get-accounts"),
  createAccount: (input: unknown) => ipcRenderer.invoke("create-account", input),
  updateAccountStatus: (accountId: string, status: string, message?: string) => ipcRenderer.invoke("update-account-status", accountId, status, message),
  revealAccount: (accountId: string) => ipcRenderer.invoke("reveal-account", accountId),
  deleteAccount: (accountId: string) => ipcRenderer.invoke("delete-account", accountId),
  getShops: () => ipcRenderer.invoke("get-shops"),
  warmupMediamarkt: (profileId?: string) => ipcRenderer.invoke("warmup-mediamarkt", profileId),
  mmSessionCookies: () => ipcRenderer.invoke("mm-session-cookies"),
  registerShop: (config: unknown) => ipcRenderer.invoke("register-shop", config),
  createTask: (config: unknown) => ipcRenderer.invoke("create-task", config),
  setPaymentSession: (taskId: string, payment: unknown) => ipcRenderer.invoke("set-payment-session", taskId, payment),
  clearPaymentSession: (taskId: string) => ipcRenderer.invoke("clear-payment-session", taskId),
  startTask: (taskId: string) => ipcRenderer.invoke("start-task", taskId),
  pauseTask: (taskId: string) => ipcRenderer.invoke("pause-task", taskId),
  resumeTask: (taskId: string) => ipcRenderer.invoke("resume-task", taskId),
  stopTask: (taskId: string) => ipcRenderer.invoke("stop-task", taskId),
  deleteTask: (taskId: string) => ipcRenderer.invoke("delete-task", taskId),
  restartTask: (taskId: string) => ipcRenderer.invoke("restart-task", taskId),
  updateDiscoveryKeywords: (taskId: string, keywords: string[]) => ipcRenderer.invoke("update-discovery-keywords", taskId, keywords),
  getFinalPurchaseSetting: () => ipcRenderer.invoke("get-final-purchase-setting"),
  setFinalPurchaseAllowed: (allowed: boolean) => ipcRenderer.invoke("set-final-purchase-allowed", allowed),
  getTaskStatus: (taskId: string) => ipcRenderer.invoke("get-task-status", taskId),
  getTaskList: () => ipcRenderer.invoke("get-task-list"),
  getTaskLogs: (taskId: string, limit = 100) => ipcRenderer.invoke("get-task-logs", taskId, limit),
  getProductMonitorEvents: (taskId: string, limit = 100) => ipcRenderer.invoke("get-product-monitor-events", taskId, limit),
  getSystemStatus: () => ipcRenderer.invoke("get-system-status"),
  testCapmonsterApiKey: () => ipcRenderer.invoke("test-capmonster-api-key"),
  listCaptchaProviders: () => ipcRenderer.invoke("list-captcha-providers"),
  saveCaptchaProvider: (id: string, input: unknown) => ipcRenderer.invoke("save-captcha-provider", id, input),
  deleteCaptchaProvider: (id: string) => ipcRenderer.invoke("delete-captcha-provider", id),
  testCaptchaProvider: (id: string) => ipcRenderer.invoke("test-captcha-provider", id),
  getCaptchaMode: () => ipcRenderer.invoke("get-captcha-mode"),
  setCaptchaMode: (mode: string) => ipcRenderer.invoke("set-captcha-mode", mode),
  onTaskStatusUpdate: (callback: (task: unknown) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, payload: unknown) => callback(payload);
    taskStatusListeners.set(callback, listener);
    ipcRenderer.on("task-status-update", listener);
    return () => {
      ipcRenderer.removeListener("task-status-update", listener);
      taskStatusListeners.delete(callback);
    };
  },
  onProductMonitorUpdate: (callback: (payload: unknown) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, payload: unknown) => callback(payload);
    monitorListeners.set(callback, listener);
    ipcRenderer.on("product-monitor-update", listener);
    return () => {
      ipcRenderer.removeListener("product-monitor-update", listener);
      monitorListeners.delete(callback);
    };
  },
  onLiveLog: (callback: (payload: unknown) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, payload: unknown) => callback(payload);
    liveLogListeners.set(callback, listener);
    ipcRenderer.on("live-log", listener);
    return () => {
      ipcRenderer.removeListener("live-log", listener);
      liveLogListeners.delete(callback);
    };
  },
  removeTaskStatusListener: (callback?: (task: unknown) => void) => {
    if (callback && taskStatusListeners.has(callback)) {
      const listener = taskStatusListeners.get(callback)!;
      ipcRenderer.removeListener("task-status-update", listener);
      taskStatusListeners.delete(callback);
    } else {
      ipcRenderer.removeAllListeners("task-status-update");
      taskStatusListeners.clear();
    }
  },
  removeProductMonitorListener: (callback?: (payload: unknown) => void) => {
    if (callback && monitorListeners.has(callback)) {
      const listener = monitorListeners.get(callback)!;
      ipcRenderer.removeListener("product-monitor-update", listener);
      monitorListeners.delete(callback);
    } else {
      ipcRenderer.removeAllListeners("product-monitor-update");
      monitorListeners.clear();
    }
  }
};

contextBridge.exposeInMainWorld("ares", api);
export type AresApi = typeof api;
