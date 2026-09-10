import {
  IBrowserManager,
  IProxyManager,
  IShopAdapter,
  ITaskExecutor,
  ITaskRepository,
  IWorker
} from "../interfaces";
import { Task } from "../models";

export class TaskRepositoryMock implements ITaskRepository {
  private readonly tasks = new Map<string, Task>();

  async save(task: Task): Promise<void> {
    this.tasks.set(task.id, structuredClone(task));
  }

  async findById(id: string): Promise<Task | null> {
    return this.tasks.get(id) ?? null;
  }

  async findAll(): Promise<Task[]> {
    return [...this.tasks.values()];
  }

  async update(task: Task): Promise<void> {
    this.tasks.set(task.id, structuredClone(task));
  }

  async delete(id: string): Promise<void> {
    this.tasks.delete(id);
  }
}

export class BrowserManagerMock implements IBrowserManager {
  async launchBrowser(): Promise<void> {}
  async closeBrowser(): Promise<void> {}
  async navigateTo(_url: string): Promise<void> {}
  async waitForSelector(_selector: string, _timeout?: number): Promise<void> {}
}

export class ShopAdapterMock implements IShopAdapter {
  async findProduct(_task: Task): Promise<boolean> { return true; }
  async addToCart(_task: Task): Promise<boolean> { return true; }
  async checkout(_task: Task): Promise<boolean> { return true; }
}

export class ProxyManagerMock implements IProxyManager {
  private readonly proxies = ["mock-proxy-1", "mock-proxy-2"];
  private readonly used = new Set<string>();

  async getProxy(): Promise<string> {
    const proxy = this.proxies.find(p => !this.used.has(p));
    if (!proxy) throw new Error("No mock proxy available");
    this.used.add(proxy);
    return proxy;
  }

  releaseProxy(proxy: string): void {
    this.used.delete(proxy);
  }
}

export class TaskExecutorMock implements ITaskExecutor {
  async execute(_task: Task): Promise<boolean> {
    return true;
  }
}

export class WorkerMock implements IWorker {
  status: "idle" | "busy" = "idle";
  currentTaskId?: string;

  constructor(public readonly id: string) {}

  async run(_task: Task): Promise<void> {}
  stop(): void {
    this.status = "idle";
    this.currentTaskId = undefined;
  }
}
