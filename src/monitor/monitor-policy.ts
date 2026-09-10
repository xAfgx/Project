import type { Task } from "../models";
import type { CommerceShop } from "../commerce/platforms";

export type MonitorNetworkMode = "session-http-preferred" | "browser-only";

export interface MonitorPolicy {
  networkMode: MonitorNetworkMode;
  allowPassiveNetwork: boolean;
  allowPassiveDom: boolean;
  allowActiveBrowserFallback: boolean;
}

function record(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function bool(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

export function resolveMonitorPolicy(task: Task, shop: CommerceShop): MonitorPolicy {
  const taskData = task.config.data ?? {};
  const taskPolicy = record(taskData["monitorPolicy"]);
  const shopPolicy = record(shop.config?.["monitorPolicy"]);
  const legacyTaskMode = String(taskData["monitorNetworkMode"] ?? "").trim().toLowerCase();
  const legacyShopMode = String(shop.config?.["monitorNetworkMode"] ?? "").trim().toLowerCase();
  const configuredMode = String(
    taskPolicy?.["networkMode"]
      ?? (legacyTaskMode || undefined)
      ?? shopPolicy?.["networkMode"]
      ?? legacyShopMode
  ).trim().toLowerCase();
  const networkMode: MonitorNetworkMode = configuredMode === "browser-only" || configuredMode === "strict" || configuredMode === "high-security"
    ? "browser-only"
    : "session-http-preferred";

  return {
    networkMode,
    allowPassiveNetwork: bool(taskPolicy?.["allowPassiveNetwork"], bool(shopPolicy?.["allowPassiveNetwork"], true)),
    allowPassiveDom: bool(taskPolicy?.["allowPassiveDom"], bool(shopPolicy?.["allowPassiveDom"], true)),
    allowActiveBrowserFallback: bool(taskPolicy?.["allowActiveBrowserFallback"], bool(shopPolicy?.["allowActiveBrowserFallback"], true))
  };
}
