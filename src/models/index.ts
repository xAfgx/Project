export interface TaskConfig {
  id: string;
  name: string;
  shopId?: string;
  maxRetries?: number;
  timeout?: number;
  data?: Record<string, unknown>;
}

export enum TaskState {
  CREATED = "CREATED",
  QUEUED = "QUEUED",
  STARTING = "STARTING",
  RUNNING = "RUNNING",
  WAITING_QUEUE = "WAITING_QUEUE",
  POST_QUEUE_DISCOVERY = "POST_QUEUE_DISCOVERY",
  PAUSED = "PAUSED",
  PRODUCT_FOUND = "PRODUCT_FOUND",
  CART = "CART",
  CHECKOUT = "CHECKOUT",
  SUCCESS = "SUCCESS",
  FAILED = "FAILED",
  CANCELLED = "CANCELLED",
  RETRYING = "RETRYING"
}

export interface Task {
  id: string;
  config: TaskConfig;
  state: TaskState;
  createdAt: Date;
  updatedAt: Date;
  lastError?: string;
  retries: number;
  maxRetries: number;
}

export type TaskLogLevel = "info" | "warn" | "error";

export interface TaskLogEntry {
  id?: number;
  taskId: string;
  event: string;
  state?: TaskState;
  level: TaskLogLevel;
  message: string;
  createdAt: Date;
}
