import { Task, TaskConfig, TaskLogEntry, TaskState } from "../models";
import type { ProductMonitorEvent } from "../monitor/models";

export interface ITaskExecutor {
  execute(task: Task): Promise<boolean>;
  cancelTask?(taskId: string): Promise<void>;
  updateDiscoveryKeywords?(taskId: string, keywords: string[]): Promise<string[]>;
  setFinalPurchaseAllowed?(allowed: boolean): Promise<void>;
  close?(): void | Promise<void>;
}

export interface IBrowserManager {
  launchBrowser(): Promise<void>;
  closeBrowser(): Promise<void>;
  navigateTo(url: string): Promise<void>;
  waitForSelector(selector: string, timeout?: number): Promise<void>;
}

export interface IShopAdapter {
  findProduct(task: Task): Promise<boolean>;
  addToCart(task: Task): Promise<boolean>;
  checkout(task: Task): Promise<boolean>;
}

export interface IProxyManager {
  getProxy(): Promise<string>;
  releaseProxy(proxy: string): void;
}

export interface ITaskRepository {
  save(task: Task): Promise<void>;
  findById(id: string): Promise<Task | null>;
  findAll(): Promise<Task[]>;
  update(task: Task): Promise<void>;
  delete(id: string): Promise<void>;
}

export interface ITaskLogRepository {
  appendLog(entry: TaskLogEntry): Promise<void>;
  findLogsByTaskId(taskId: string, limit?: number): Promise<TaskLogEntry[]>;
  deleteLogsByTaskId(taskId: string): Promise<void>;
}

export interface StoredProductMonitorEvent extends ProductMonitorEvent {
  id?: number;
  taskId: string;
}

export interface IProductMonitorEventRepository {
  recordProductMonitorEvent(taskId: string, event: ProductMonitorEvent): Promise<void>;
  findProductMonitorEventsByTaskId(taskId: string, limit?: number): Promise<StoredProductMonitorEvent[]>;
  deleteProductMonitorEventsByTaskId(taskId: string): Promise<void>;
}

export interface ITaskPersistenceRepository
  extends ITaskRepository, ITaskLogRepository, IProductMonitorEventRepository {
  recordTaskEvent(task: Task, entry: TaskLogEntry): Promise<void>;
  close?(): Promise<void>;
}

export interface IWorker {
  id: string;
  status: "idle" | "busy";
  currentTaskId?: string;
  run(task: Task): Promise<void>;
  stop(): void;
}

export interface TaskEvents {
  taskCreated: Task;
  taskUpdated: Task;
  taskDeleted: string;
  taskQueued: Task;
  taskStarted: Task;
  taskPaused: Task;
  taskCancelled: Task;
  taskResumed: Task;
  taskCompleted: Task;
  taskFailed: Task;
  taskRetrying: Task;
  taskStateChanged: {
    task: Task;
    previousState: TaskState;
    newState: TaskState;
  };
  workerAssigned: { taskId: string; workerId: string };
  workerReleased: { taskId: string; workerId: string };
  workerAdded: IWorker;
  workerRemoved: string;
}
