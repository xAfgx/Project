export type RuntimeDebugLevel = "debug" | "info" | "warn" | "error";

export interface RuntimeDebugEvent {
  sequence: number;
  timestamp: string;
  source: string;
  event: string;
  level: RuntimeDebugLevel;
  taskId?: string;
  pid?: number;
  durationMs?: number;
  attempt?: number;
  message?: string;
}

export interface RuntimeDebugEventInput {
  source: string;
  event: string;
  level?: RuntimeDebugLevel;
  taskId?: string;
  pid?: number;
  durationMs?: number;
  attempt?: number;
  message?: string;
}

export class RuntimeDebugBus {
  private readonly listeners = new Set<(events: RuntimeDebugEvent[]) => void>();
  private readonly pending: RuntimeDebugEvent[] = [];
  private flushTimer?: NodeJS.Timeout;
  private nextSequence = 1;
  private droppedEvents = 0;

  constructor(
    private readonly flushIntervalMs = 75,
    private readonly maxPendingEvents = 2_000
  ) {}

  emit(input: RuntimeDebugEventInput): void {
    try {
      if (!input?.source || !input?.event) return;
      if (this.pending.length >= this.maxPendingEvents) {
        this.droppedEvents += 1;
        return;
      }

      this.pending.push({
        sequence: this.nextSequence++,
        timestamp: new Date().toISOString(),
        source: String(input.source).slice(0, 48),
        event: String(input.event).slice(0, 96),
        level: input.level ?? "info",
        ...(input.taskId ? { taskId: String(input.taskId).slice(0, 128) } : {}),
        ...(typeof input.pid === "number" && Number.isFinite(input.pid) ? { pid: Math.trunc(input.pid) } : {}),
        ...(typeof input.durationMs === "number" && Number.isFinite(input.durationMs) ? { durationMs: Math.max(0, Math.trunc(input.durationMs)) } : {}),
        ...(typeof input.attempt === "number" && Number.isFinite(input.attempt) ? { attempt: Math.max(0, Math.trunc(input.attempt)) } : {}),
        ...(input.message ? { message: String(input.message).slice(0, 500) } : {})
      });

      this.scheduleFlush();
    } catch {
      // Observability is fail-open by design. Runtime must never fail because logging failed.
    }
  }

  subscribe(listener: (events: RuntimeDebugEvent[]) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  close(): void {
    if (this.flushTimer) clearTimeout(this.flushTimer);
    this.flushTimer = undefined;
    this.pending.length = 0;
    this.listeners.clear();
  }

  private scheduleFlush(): void {
    if (this.flushTimer) return;
    this.flushTimer = setTimeout(() => this.flush(), this.flushIntervalMs);
    this.flushTimer.unref?.();
  }

  private flush(): void {
    this.flushTimer = undefined;
    if (this.pending.length === 0 && this.droppedEvents === 0) return;

    const batch = this.pending.splice(0);
    if (this.droppedEvents > 0) {
      batch.push({
        sequence: this.nextSequence++,
        timestamp: new Date().toISOString(),
        source: "DEBUG",
        event: "events.dropped",
        level: "warn",
        message: `${this.droppedEvents} debug events dropped due to backpressure`
      });
      this.droppedEvents = 0;
    }

    for (const listener of this.listeners) {
      try { listener(batch); } catch { /* fail-open */ }
    }
  }
}
