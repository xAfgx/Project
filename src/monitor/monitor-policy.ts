import type { Task } from "../models";
import type { CommerceShop } from "../commerce/platforms";

export type MonitorNetworkMode = "session-http-preferred" | "browser-only";
export type MonitorEngine = "curl_cffi" | "cdp";

export interface MonitorPolicy {
  networkMode: MonitorNetworkMode;
  engine: MonitorEngine;
  sessionHttpRequested: boolean;
  sessionHttpAllowed: boolean;
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

// Strict engine switch: curl_cffi is only permitted when the instance was
// explicitly started as MONITOR through the UI. Manual browsers, live tests,
// early-gate, checkout tasks and every other lane are locked to the native
// browser infrastructure (engine=cdp, harvest=cdp, no cookie jar sync).
const UI_MONITOR_ACTIONS = new Set(["monitor-only"]);

export function isExplicitUiMonitorInstance(task: Task): boolean {
  const action = record((task.config.data ?? {})["monitorAction"]);
  return UI_MONITOR_ACTIONS.has(String(action?.["mode"] ?? "").trim().toLowerCase());
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
  const sessionHttpRequested = configuredMode !== "browser-only"
    && configuredMode !== "strict"
    && configuredMode !== "high-security";
  const sessionHttpAllowed = sessionHttpRequested && isExplicitUiMonitorInstance(task);
  const networkMode: MonitorNetworkMode = sessionHttpAllowed ? "session-http-preferred" : "browser-only";
  const engine: MonitorEngine = sessionHttpAllowed ? "curl_cffi" : "cdp";

  return {
    networkMode,
    engine,
    sessionHttpRequested,
    sessionHttpAllowed,
    allowPassiveNetwork: bool(taskPolicy?.["allowPassiveNetwork"], bool(shopPolicy?.["allowPassiveNetwork"], true)),
    allowPassiveDom: bool(taskPolicy?.["allowPassiveDom"], bool(shopPolicy?.["allowPassiveDom"], true)),
    allowActiveBrowserFallback: bool(taskPolicy?.["allowActiveBrowserFallback"], bool(shopPolicy?.["allowActiveBrowserFallback"], true))
  };
}
