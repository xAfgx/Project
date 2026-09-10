import type { Task } from "../models";

export type MonitorStrategyMode = "product-monitor" | "early-gate";
export type EarlyGateActiveArea = "monitor" | "gate" | "browser-child";
export type EarlyGateFlowStage =
  | "monitoring"
  | "gate-detected"
  | "browser-child"
  | "post-queue-discovery"
  | "product-found"
  | "cart"
  | "checkout";

export interface EarlyGateMonitorStrategy {
  mode: "early-gate";
  productName: string;
  discoveryKeywords: string[];
}

export interface ProductMonitorStrategy {
  mode: "product-monitor";
}

export type MonitorStrategy = EarlyGateMonitorStrategy | ProductMonitorStrategy;

export interface PreCheckoutGateEvent {
  type: "early-drop-signal" | "queue-signal";
  shopId: string;
  observedAt: Date;
  source: string;
  position?: number;
  timeToWaitSeconds?: number;
  statusText?: string;
}

export interface EarlyGateRuntime {
  flowId: string;
  activeArea: EarlyGateActiveArea;
  stage: EarlyGateFlowStage;
  parentTaskId: string;
  childTaskId?: string;
  productName: string;
  keywords: string[];
  monitoringAt?: string;
  gateDetectedAt?: string;
  browserChildStartedAt?: string;
  queueEnteredAt?: string;
  queueReleasedAt?: string;
  postQueueDiscoveryAt?: string;
  productFoundAt?: string;
  cartAt?: string;
  checkoutAt?: string;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

export function normalizeDiscoveryKeywords(values: unknown): string[] {
  if (!Array.isArray(values)) return [];
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of values) {
    const normalized = String(value ?? "").trim().replace(/\s+/g, " ");
    if (!normalized) continue;
    const key = normalized.toLocaleLowerCase("de-DE");
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(normalized.slice(0, 160));
    if (result.length >= 24) break;
  }
  return result;
}

export function getMonitorStrategy(task: Task): MonitorStrategy {
  const raw = asRecord(task.config.data?.["monitorStrategy"]);
  if (raw?.["mode"] !== "early-gate") return { mode: "product-monitor" };
  return {
    mode: "early-gate",
    productName: String(raw["productName"] ?? "").trim().slice(0, 240),
    discoveryKeywords: normalizeDiscoveryKeywords(raw["discoveryKeywords"])
  };
}

export function isEarlyGateMonitorTask(task: Task): boolean {
  return getMonitorStrategy(task).mode === "early-gate";
}

export function isEarlyGateChildTask(task: Task): boolean {
  const trigger = asRecord(task.config.data?.["triggerSource"]);
  return trigger?.["kind"] === "early-gate" && Boolean(trigger["parentTaskId"]);
}

export function getEarlyGateRuntime(task: Task): EarlyGateRuntime | undefined {
  return asRecord(task.config.data?.["earlyGateRuntime"]) as unknown as EarlyGateRuntime | undefined;
}

export function setEarlyGateRuntime(task: Task, patch: Partial<EarlyGateRuntime>): EarlyGateRuntime {
  const existing = getEarlyGateRuntime(task);
  const strategy = getMonitorStrategy(task);
  const trigger = asRecord(task.config.data?.["triggerSource"]);
  const postQueue = asRecord(task.config.data?.["postQueueDiscovery"]);
  const parentTaskId = String(patch.parentTaskId ?? existing?.parentTaskId ?? trigger?.["parentTaskId"] ?? task.id);
  const productName = String(
    patch.productName ?? existing?.productName ?? postQueue?.["productName"] ?? (strategy.mode === "early-gate" ? strategy.productName : "")
  );
  const keywords = normalizeDiscoveryKeywords(
    patch.keywords ?? existing?.keywords ?? postQueue?.["keywords"] ?? (strategy.mode === "early-gate" ? strategy.discoveryKeywords : [])
  );
  const runtime: EarlyGateRuntime = {
    flowId: String(patch.flowId ?? existing?.flowId ?? `early-gate:${parentTaskId}`),
    activeArea: patch.activeArea ?? existing?.activeArea ?? "monitor",
    stage: patch.stage ?? existing?.stage ?? "monitoring",
    parentTaskId,
    childTaskId: patch.childTaskId ?? existing?.childTaskId,
    productName,
    keywords,
    monitoringAt: patch.monitoringAt ?? existing?.monitoringAt,
    gateDetectedAt: patch.gateDetectedAt ?? existing?.gateDetectedAt,
    browserChildStartedAt: patch.browserChildStartedAt ?? existing?.browserChildStartedAt,
    queueEnteredAt: patch.queueEnteredAt ?? existing?.queueEnteredAt,
    queueReleasedAt: patch.queueReleasedAt ?? existing?.queueReleasedAt,
    postQueueDiscoveryAt: patch.postQueueDiscoveryAt ?? existing?.postQueueDiscoveryAt,
    productFoundAt: patch.productFoundAt ?? existing?.productFoundAt,
    cartAt: patch.cartAt ?? existing?.cartAt,
    checkoutAt: patch.checkoutAt ?? existing?.checkoutAt
  };
  task.config.data = {
    ...(task.config.data ?? {}),
    earlyGateRuntime: runtime
  };
  return runtime;
}
