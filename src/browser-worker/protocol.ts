import type { Task } from "../models";
import type { AresProfile } from "../profiles/models";
import type { ProfileCookieSnapshotCookie } from "../cookies/profile-cookie-snapshot-vault";
import type { RuntimeShop } from "./runtime-types";
import type { BrowserWorkerHealth } from "./types";

export type BrowserWorkerHealthWire = Omit<BrowserWorkerHealth, "startedAt"> & { startedAt: string };

export interface ExecuteTaskRequest {
  type: "execute";
  requestId: string;
  task: Task;
  shop: RuntimeShop;
  profile: AresProfile;
  cookieSnapshot?: ProfileCookieSnapshotCookie[];
}

export interface CancelTaskRequest {
  type: "cancel";
  requestId: string;
  taskId: string;
}

export interface UpdateDiscoveryKeywordsRequest {
  type: "update-discovery-keywords";
  requestId: string;
  taskId: string;
  keywords: string[];
}

export interface SetFinalPurchasePermissionRequest {
  type: "set-final-purchase-permission";
  requestId: string;
  allowed: boolean;
}

export interface HealthRequest {
  type: "health";
  requestId: string;
}

export interface ShutdownRequest {
  type: "shutdown";
  requestId: string;
}

export type BrowserWorkerRequest =
  | ExecuteTaskRequest
  | CancelTaskRequest
  | UpdateDiscoveryKeywordsRequest
  | SetFinalPurchasePermissionRequest
  | HealthRequest
  | ShutdownRequest;

export interface ReadyMessage {
   type: "ready";
   nodeVersion: string;
   pid: number;
   headed?: boolean;
   appleHardware?: boolean;
   stealth?: boolean;
   cdpFetch?: string;
   tlsIdentity?: string;
   ja4Consistent?: boolean;
}

export interface ExecuteTaskResponse {
  type: "execute-result";
  requestId: string;
  success: boolean;
  taskPatch: {
    config: Task["config"];
    lastError?: string;
  };
}

export interface TaskUpdateResponse {
  type: "task-update";
  taskId: string;
  taskPatch: {
    config: Task["config"];
    lastError?: string;
  };
}

export interface HealthResponse {
  type: "health-result";
  requestId: string;
  health: BrowserWorkerHealthWire;
  pid: number;
  nodeVersion: string;
}

export interface AckResponse {
  type: "ack";
  requestId: string;
  keywords?: string[];
  allowFinalPurchase?: boolean;
}

export interface ErrorResponse {
  type: "error";
  requestId?: string;
  error: string;
}

export type BrowserWorkerResponse = ReadyMessage | ExecuteTaskResponse | TaskUpdateResponse | HealthResponse | AckResponse | ErrorResponse;
