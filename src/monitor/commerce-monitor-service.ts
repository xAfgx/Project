import type { ITaskExecutor } from "../interfaces";
import type { Task, TaskLogEntry } from "../models";
import type { CommerceShop } from "../commerce/platforms";
import type { CommerceProductApiRouter } from "../commerce/product-api/router";
import type { ProductCriteria, ProductMonitorEvent, ProductObservation } from "./models";
import { ProductMatcher } from "./product-matcher";
import { ProductMonitor } from "./product-monitor";
import { getMonitorStrategy, setEarlyGateRuntime, type PreCheckoutGateEvent } from "./early-gate";
import type { PreCheckoutGate } from "./pre-checkout-gate";
import type { BrowserProductFallback } from "./browser-product-fallback";
import { getDefaultBrowserProductFallback } from "./browser-product-fallback-registry";

export interface ProductMonitorEventRepository {
  recordProductMonitorEvent(taskId: string, event: ProductMonitorEvent): Promise<void>;
  findProductMonitorEventsByTaskId(taskId: string, limit?: number): Promise<Array<ProductMonitorEvent & { id?: number; taskId: string }>>;
  appendLog?(entry: TaskLogEntry): Promise<void>;
}

export interface CommerceMonitorServiceOptions {
  defaultIntervalMs?: number;
  minimumIntervalMs?: number;
  searchLimit?: number;
  preCheckoutGate?: PreCheckoutGate;
  browserFallback?: BrowserProductFallback;
  /** Optional warm-up (e.g. harvest shop cookies) before/while monitoring. */
  warmUp?: (task: Task, shop: CommerceShop, signal?: AbortSignal) => Promise<void>;
  onEvent?: (taskId: string, event: ProductMonitorEvent) => void;
  onGateEvent?: (taskId: string, event: PreCheckoutGateEvent) => void;
}

interface ActiveMonitorRun {
  controller: AbortController;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

export function getTaskProductCriteria(task: Task): ProductCriteria | undefined {
  const raw = asRecord(task.config.data?.["productCriteria"]);
  if (!raw) return undefined;

  const criteria: ProductCriteria = {};
  if (typeof raw["searchTerm"] === "string") criteria.searchTerm = raw["searchTerm"];
  if (typeof raw["sku"] === "string") criteria.sku = raw["sku"];
  if (typeof raw["gtin"] === "string") criteria.gtin = raw["gtin"];
  if (typeof raw["url"] === "string") criteria.url = raw["url"];
  if (typeof raw["requireAvailable"] === "boolean") criteria.requireAvailable = raw["requireAvailable"];
  if (typeof raw["minStock"] === "number") criteria.minStock = raw["minStock"];
  if (typeof raw["minPrice"] === "number") criteria.minPrice = raw["minPrice"];
  if (typeof raw["maxPrice"] === "number") criteria.maxPrice = raw["maxPrice"];
  if (typeof raw["minimumScore"] === "number") criteria.minimumScore = raw["minimumScore"];
  return criteria;
}

export function isCommerceMonitorTask(task: Task): boolean {
  return Boolean(asRecord(task.config.data?.["productCriteria"])) || getMonitorStrategy(task).mode === "early-gate";
}

function isRelevantChange(event: ProductMonitorEvent): boolean {
  return event.type !== "unchanged";
}

function abortableDelay(ms: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.resolve();
  return new Promise(resolve => {
    const timer = setTimeout(done, ms);
    function done(): void {
      clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    }
    signal.addEventListener("abort", done, { once: true });
  });
}

function monitorMessage(event: ProductMonitorEvent): string {
  const product = event.current.variantTitle
    ? `${event.current.title} · ${event.current.variantTitle}`
    : event.current.title;
  const stock = typeof event.current.stock === "number" ? ` · Bestand ${event.current.stock}` : "";
  const price = event.current.price ? ` · ${event.current.price.amount} ${event.current.price.currency ?? ""}`.trimEnd() : "";
  return `${product} · ${event.type}${stock}${price}`;
}

function browserFallbackNeeded(
  ranked: Array<{ observation: ProductObservation; match: ReturnType<ProductMatcher["match"]> }>
): boolean {
  if (!ranked.length) return true;
  return ranked.every(candidate => {
    const source = String(candidate.observation.attributes?.["source"] ?? "");
    const signal = String(candidate.observation.attributes?.["availabilitySignal"] ?? "");
    return source === "generic-html" && signal === "unknown";
  });
}

export class CommerceMonitorService implements ITaskExecutor {
  private readonly matcher = new ProductMatcher();
  private readonly monitors = new Map<string, ProductMonitor>();
  private readonly activeRuns = new Map<string, ActiveMonitorRun>();
  private readonly gateSignaled = new Set<string>();
  private readonly runtimeListeners = new Set<(task: Task) => void>();
  private readonly defaultIntervalMs: number;
  private readonly minimumIntervalMs: number;
  private readonly searchLimit: number;
  private readonly browserFallback?: BrowserProductFallback;

  constructor(
    private readonly getShop: (shopId: string) => CommerceShop | undefined,
    private readonly productApiRouter: Pick<CommerceProductApiRouter, "search">,
    private readonly repository: ProductMonitorEventRepository,
    private readonly options: CommerceMonitorServiceOptions = {}
  ) {
    this.defaultIntervalMs = Math.max(1, options.defaultIntervalMs ?? 30_000);
    this.minimumIntervalMs = Math.max(1, options.minimumIntervalMs ?? 1_000);
    this.searchLimit = Math.min(250, Math.max(1, options.searchLimit ?? 50));
    this.browserFallback = options.browserFallback ?? getDefaultBrowserProductFallback();
  }

  onTaskUpdate(callback: (task: Task) => void): () => void {
    this.runtimeListeners.add(callback);
    return () => this.runtimeListeners.delete(callback);
  }

  async execute(task: Task): Promise<boolean> {
    const shopId = task.config.shopId;
    if (!shopId) {
      task.lastError = "Monitoring-Task hat keine shopId.";
      return false;
    }

    const shop = this.getShop(shopId);
    if (!shop) {
      task.lastError = `Shop ${shopId} ist nicht registriert.`;
      return false;
    }

    const strategy = getMonitorStrategy(task);
    const criteria = getTaskProductCriteria(task);
    if (strategy.mode !== "early-gate" && !criteria) {
      task.lastError = "Monitoring-Task hat keine productCriteria.";
      return false;
    }
    if (strategy.mode === "early-gate" && !this.options.preCheckoutGate) {
      task.lastError = "Early-Gate ist konfiguriert, aber kein passiver PreCheckoutGate-Adapter registriert.";
      return false;
    }

    if (this.activeRuns.has(task.id)) {
      task.lastError = `Monitoring für Task ${task.id} läuft bereits.`;
      return false;
    }

    const controller = new AbortController();
    const run: ActiveMonitorRun = { controller };
    this.activeRuns.set(task.id, run);

    if (strategy.mode === "early-gate") {
      setEarlyGateRuntime(task, {
        activeArea: "monitor",
        stage: "monitoring",
        productName: strategy.productName,
        keywords: strategy.discoveryKeywords,
        monitoringAt: new Date().toISOString()
      });
      this.emitTaskUpdate(task);
    }

    try {
      // Warm start: never begin cold. Optional hook (e.g. MediaMarkt harvests
      // its cookies in a browser once) runs before the first API poll and is
      // refreshed every 30 minutes.
      await this.options.warmUp?.(task, shop, controller.signal).catch(() => undefined);
      let lastWarmUpAt = Date.now();
      while (!controller.signal.aborted) {
        if (strategy.mode === "early-gate") {
          await this.runGateCycle(task, shop, controller.signal);
        } else {
          await this.runCycle(task, shop, criteria, controller.signal);
        }
        task.lastError = undefined;
        if (controller.signal.aborted) break;
        if (this.options.warmUp && Date.now() - lastWarmUpAt >= 30 * 60_000) {
          await this.options.warmUp(task, shop, controller.signal).catch(() => undefined);
          lastWarmUpAt = Date.now();
        }
        await abortableDelay(this.intervalFor(task), controller.signal);
      }
      task.lastError = undefined;
      return true;
    } catch (error) {
      if (controller.signal.aborted) return true;
      task.lastError = error instanceof Error ? error.message : String(error);
      this.emitTaskUpdate(task);
      return false;
    } finally {
      if (this.activeRuns.get(task.id) === run) this.activeRuns.delete(task.id);
      await this.browserFallback?.cancelTask(task.id).catch(() => undefined);
    }
  }

  async runGateCycle(task: Task, shop?: CommerceShop, signal?: AbortSignal): Promise<PreCheckoutGateEvent | undefined> {
    if (this.gateSignaled.has(task.id)) return undefined;
    const shopId = task.config.shopId;
    if (!shopId) throw new Error("Early-Gate-Task hat keine shopId.");
    const resolvedShop = shop ?? this.getShop(shopId);
    if (!resolvedShop) throw new Error(`Shop ${shopId} ist nicht registriert.`);
    const gate = this.options.preCheckoutGate;
    if (!gate) throw new Error("Kein passiver PreCheckoutGate-Adapter registriert.");

    const event = await gate.evaluate(task, resolvedShop, signal);
    if (!event || signal?.aborted) return undefined;
    this.gateSignaled.add(task.id);
    const gateDetectedAt = event.observedAt.toISOString();
    setEarlyGateRuntime(task, {
      activeArea: "gate",
      stage: "gate-detected",
      gateDetectedAt
    });
    task.config.data = {
      ...(task.config.data ?? {}),
      gateTelemetry: {
        source: event.source,
        position: event.position,
        timeToWaitSeconds: event.timeToWaitSeconds,
        statusText: event.statusText,
        observedAt: gateDetectedAt
      }
    };
    this.emitTaskUpdate(task);
    this.options.onGateEvent?.(task.id, event);
    return event;
  }

  async runCycle(
    task: Task,
    shop?: CommerceShop,
    criteria?: ProductCriteria,
    signal?: AbortSignal
  ): Promise<ProductMonitorEvent[]> {
    const resolvedShopId = task.config.shopId;
    if (!resolvedShopId) throw new Error("Monitoring-Task hat keine shopId.");

    const resolvedShop = shop ?? this.getShop(resolvedShopId);
    if (!resolvedShop) throw new Error(`Shop ${resolvedShopId} ist nicht registriert.`);

    const resolvedCriteria = criteria ?? getTaskProductCriteria(task);
    if (!resolvedCriteria) throw new Error("Monitoring-Task hat keine productCriteria.");

    let observations: ProductObservation[] = [];
    let fastPathError: string | undefined;
    try {
      observations = await this.productApiRouter.search(resolvedShop, resolvedCriteria, this.searchLimit);
    } catch (error) {
      fastPathError = error instanceof Error ? error.message : String(error);
      observations = [];
    }
    if (signal?.aborted) return [];

    let ranked = observations
      .map(observation => ({ observation, match: this.matcher.match(observation, resolvedCriteria) }))
      .filter(candidate => candidate.match.matched)
      .sort((a, b) => b.match.score - a.match.score);

    if (this.browserFallback && browserFallbackNeeded(ranked) && !signal?.aborted) {
      if (fastPathError && this.repository.appendLog) {
        await this.repository.appendLog({
          taskId: task.id,
          event: "monitor:fast-path-error",
          state: task.state,
          level: "warn",
          message: `HTTP Fast-Path fehlgeschlagen (${fastPathError}) – falle auf Browser-Rendering zurück.`,
          createdAt: new Date()
        });
      }
      const rendered = await this.browserFallback.search(
        task,
        resolvedShop,
        resolvedCriteria,
        this.searchLimit,
        signal
      );
      if (signal?.aborted) return [];
      if (rendered.length) {
        observations = rendered;
        ranked = observations
          .map(observation => ({ observation, match: this.matcher.match(observation, resolvedCriteria) }))
          .filter(candidate => candidate.match.matched)
          .sort((a, b) => b.match.score - a.match.score);
      }
    }

    const monitor = this.monitorFor(task.id);
    const relevantEvents: ProductMonitorEvent[] = [];

    for (const candidate of ranked) {
      if (signal?.aborted) break;
      const event = monitor.observe(candidate.observation, resolvedCriteria);
      if (!event || !isRelevantChange(event)) continue;

      await this.repository.recordProductMonitorEvent(task.id, event);
      if (this.repository.appendLog) {
        await this.repository.appendLog({
          taskId: task.id,
          event: `product:${event.type}`,
          state: task.state,
          level: "info",
          message: monitorMessage(event),
          createdAt: new Date(event.observedAt)
        });
      }
      relevantEvents.push(event);
      this.options.onEvent?.(task.id, event);
    }

    // Heartbeat so the live console shows the monitor is polling even when
    // nothing changed.
    process.stderr.write(`[MONITOR] cycle shop=${resolvedShop.id} matched=${ranked.length} events=${relevantEvents.length}\n`);
    return relevantEvents;
  }

  async cancelTask(taskId: string): Promise<void> {
    this.activeRuns.get(taskId)?.controller.abort();
    await this.browserFallback?.cancelTask(taskId).catch(() => undefined);
  }

  resetTask(taskId: string): void {
    this.monitors.delete(taskId);
    this.gateSignaled.delete(taskId);
  }

  async close(): Promise<void> {
    for (const run of this.activeRuns.values()) run.controller.abort();
    this.activeRuns.clear();
    this.monitors.clear();
    this.gateSignaled.clear();
    this.runtimeListeners.clear();
    await this.browserFallback?.close().catch(() => undefined);
  }

  private emitTaskUpdate(task: Task): void {
    for (const listener of this.runtimeListeners) listener(task);
  }

  private monitorFor(taskId: string): ProductMonitor {
    let monitor = this.monitors.get(taskId);
    if (!monitor) {
      monitor = new ProductMonitor(this.matcher);
      this.monitors.set(taskId, monitor);
    }
    return monitor;
  }

  private intervalFor(task: Task): number {
    const configured = Number(task.config.data?.["monitorIntervalMs"] ?? this.defaultIntervalMs);
    if (!Number.isFinite(configured)) return this.defaultIntervalMs;
    return Math.max(this.minimumIntervalMs, Math.floor(configured));
  }
}
