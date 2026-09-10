import type { BrowserContextConfig, BrowserContextHandle, BrowserWorkerHealth } from "./types";

export interface BrowserWorker {
  createContext(config: BrowserContextConfig): Promise<BrowserContextHandle>;
  closeContext(taskId: string): Promise<void>;
  health(): Promise<BrowserWorkerHealth>;
}
