export {};

declare global {
  interface Window {
    ares: {
      getProfiles(): Promise<any>;
      saveProfile(profile: unknown): Promise<any>;
      deleteProfile(profileId: string): Promise<any>;
      getProfilePayment(profileId: string): Promise<any>;
      saveProfilePayment(profileId: string, payment: unknown): Promise<any>;
      deleteProfilePayment(profileId: string): Promise<any>;
      getProfileBrowserStatus(profileId: string): Promise<any>;
      openProfileBrowser(profileId: string, startUrl?: string): Promise<any>;
      closeProfileBrowser(profileId: string): Promise<any>;
      resetProfileBrowserSession(profileId: string): Promise<any>;
      getSeleniumBaseProfileBrowserStatus(profileId: string): Promise<any>;
      openSeleniumBaseProfileBrowser(profileId: string, startUrl?: string, cookieSnapshotId?: string): Promise<any>;
      closeSeleniumBaseProfileBrowser(profileId: string): Promise<any>;
      applySeleniumBaseCookieSnapshot(profileId: string, snapshotId: string): Promise<any>;
      saveSeleniumBaseProfileCookieSnapshot(profileId: string, name: string, snapshotId?: string): Promise<any>;
      listProfileCookieSnapshots(profileId: string): Promise<any>;
      saveProfileCookieSnapshot(profileId: string, name: string, snapshotId?: string): Promise<any>;
      deleteProfileCookieSnapshot(profileId: string, snapshotId: string): Promise<any>;
      getProxies(): Promise<any>;
      saveProxy(proxy: unknown): Promise<any>;
      testProxy(proxyId: string): Promise<any>;
      testAllProxies(): Promise<any>;
      deleteProxy(proxyId: string): Promise<any>;
      getShops(): Promise<any>;
      registerShop(config: unknown): Promise<any>;
      createTask(config: unknown): Promise<any>;
      setPaymentSession(taskId: string, payment: unknown): Promise<any>;
      clearPaymentSession(taskId: string): Promise<any>;
      startTask(taskId: string): Promise<any>;
      pauseTask(taskId: string): Promise<any>;
      resumeTask(taskId: string): Promise<any>;
      stopTask(taskId: string): Promise<any>;
      deleteTask(taskId: string): Promise<any>;
      restartTask(taskId: string): Promise<any>;
      updateDiscoveryKeywords(taskId: string, keywords: string[]): Promise<any>;
      getFinalPurchaseSetting(): Promise<any>;
      setFinalPurchaseAllowed(allowed: boolean): Promise<any>;
      getTaskStatus(taskId: string): Promise<any>;
      getTaskList(): Promise<any>;
      getTaskLogs(taskId: string, limit?: number): Promise<any>;
      getProductMonitorEvents(taskId: string, limit?: number): Promise<any>;
      getSystemStatus(): Promise<any>;
      testCapmonsterApiKey(): Promise<any>;
      onTaskStatusUpdate(callback: (task: unknown) => void): () => void;
      onProductMonitorUpdate(callback: (payload: unknown) => void): () => void;
      removeTaskStatusListener?(callback?: (task: unknown) => void): void;
      removeProductMonitorListener?(callback?: (payload: unknown) => void): void;
    };
  }
}
