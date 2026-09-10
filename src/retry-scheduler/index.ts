import { EventBus } from "../event-bus";
import { Task } from "../models";

export class RetryScheduler {
  private readonly scheduled = new Map<string, ReturnType<typeof setTimeout>>();

  constructor(private readonly eventBus: EventBus) {}

  scheduleRetry(task: Task, delayMs = 5000): void {
    this.cancelRetry(task.id);

    const timer = setTimeout(() => {
      this.scheduled.delete(task.id);
      this.eventBus.emit("taskRetrying", task);
    }, delayMs);

    this.scheduled.set(task.id, timer);
  }

  cancelRetry(taskId: string): void {
    const timer = this.scheduled.get(taskId);
    if (timer) {
      clearTimeout(timer);
      this.scheduled.delete(taskId);
    }
  }

  cleanup(): void {
    for (const timer of this.scheduled.values()) clearTimeout(timer);
    this.scheduled.clear();
  }
}