import { Component, NgZone, OnDestroy, OnInit } from "@angular/core";

type DebugLevel = "info" | "warn" | "error" | "debug";

interface LiveDebugEvent {
  id: number;
  at: Date;
  source: string;
  event: string;
  level: DebugLevel;
  taskId?: string;
  message: string;
}

@Component({
  selector: "app-runtime-debug-console",
  templateUrl: "./runtime-debug-console.component.html",
  styleUrls: ["./runtime-debug-console.component.scss"]
})
export class RuntimeDebugConsoleComponent implements OnInit, OnDestroy {
  open = false;
  paused = false;
  filter = "";
  events: LiveDebugEvent[] = [];

  private readonly maxEvents = 500;
  private nextId = 1;
  private pending: LiveDebugEvent[] = [];
  private flushTimer?: ReturnType<typeof setTimeout>;
  private unsubscribeTask?: () => void;
  private unsubscribeMonitor?: () => void;
  private readonly onDocumentClick = (event: MouseEvent) => this.captureUiClick(event);

  constructor(private readonly zone: NgZone) {}

  ngOnInit(): void {
    const api = (window as any).ares;

    this.zone.runOutsideAngular(() => {
      if (api?.onTaskStatusUpdate) {
        this.unsubscribeTask = api.onTaskStatusUpdate((task: any) => {
          this.enqueue({
            source: "TASK",
            event: "task.status",
            level: task?.lastError ? "warn" : "info",
            taskId: String(task?.id ?? "") || undefined,
            message: `${String(task?.state ?? "UNKNOWN")}${task?.lastError ? ` · ${String(task.lastError)}` : ""}`
          });
        });
      }

      if (api?.onProductMonitorUpdate) {
        this.unsubscribeMonitor = api.onProductMonitorUpdate((payload: any) => {
          const taskId = String(payload?.taskId ?? "") || undefined;
          const eventName = payload?.gateEvent
            ? "monitor.gate"
            : payload?.event
              ? "monitor.event"
              : payload?.browserMonitor
                ? "monitor.browser"
                : "monitor.update";
          const detail = payload?.gateEvent?.type
            ?? payload?.event?.type
            ?? payload?.browserMonitor?.status
            ?? "update";
          this.enqueue({ source: "MONITOR", event: eventName, level: "info", taskId, message: String(detail) });
        });
      }

      // Passive capture observes UI intent without altering click handling or running Angular change detection per click.
      document.addEventListener("click", this.onDocumentClick, { capture: true, passive: true });
      this.enqueue({ source: "DEBUG", event: "console.ready", level: "info", message: "Live debug stream ready" });
    });
  }

  ngOnDestroy(): void {
    this.unsubscribeTask?.();
    this.unsubscribeMonitor?.();
    document.removeEventListener("click", this.onDocumentClick, true);
    if (this.flushTimer) clearTimeout(this.flushTimer);
  }

  get visibleEvents(): LiveDebugEvent[] {
    const needle = this.filter.trim().toLowerCase();
    if (!needle) return this.events;
    return this.events.filter(item =>
      `${item.source} ${item.event} ${item.taskId ?? ""} ${item.message}`.toLowerCase().includes(needle)
    );
  }

  toggle(): void {
    this.open = !this.open;
  }

  clear(): void {
    this.events = [];
  }

  private captureUiClick(event: MouseEvent): void {
    const target = event.target instanceof Element ? event.target : undefined;
    if (!target) return;
    if (target.closest("app-runtime-debug-console")) return;

    const interactive = target.closest("button, a, input, select, textarea, [role='button']") as HTMLElement | null;
    if (!interactive) return;

    const tag = interactive.tagName.toLowerCase();
    const role = interactive.getAttribute("role");
    const name = interactive.getAttribute("aria-label")
      || interactive.getAttribute("name")
      || interactive.getAttribute("type")
      || interactive.id
      || tag;

    this.enqueue({
      source: "UI",
      event: "click",
      level: "debug",
      message: `${tag}${role ? `[role=${role}]` : ""} ${name}`
    });
  }

  private enqueue(input: Omit<LiveDebugEvent, "id" | "at">): void {
    try {
      if (this.paused) return;
      this.pending.push({ id: this.nextId++, at: new Date(), ...input });
      if (this.pending.length > 1000) this.pending.splice(0, this.pending.length - 1000);
      if (!this.flushTimer) {
        this.flushTimer = setTimeout(() => this.flush(), 100);
      }
    } catch {
      // Debugging must never affect runtime behavior.
    }
  }

  private flush(): void {
    this.flushTimer = undefined;
    if (this.pending.length === 0) return;
    const batch = this.pending.splice(0);
    this.zone.run(() => {
      const merged = this.events.concat(batch);
      this.events = merged.length > this.maxEvents ? merged.slice(merged.length - this.maxEvents) : merged;
    });
  }
}
