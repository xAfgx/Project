export class CancellationManager {
  private readonly controllers = new Map<string, AbortController>();

  createCancellation(taskId: string): AbortSignal {
    this.cancelTask(taskId);
    const controller = new AbortController();
    this.controllers.set(taskId, controller);
    return controller.signal;
  }

  getSignal(taskId: string): AbortSignal | undefined {
    return this.controllers.get(taskId)?.signal;
  }

  cancelTask(taskId: string): void {
    this.controllers.get(taskId)?.abort();
    this.controllers.delete(taskId);
  }

  cleanup(): void {
    for (const controller of this.controllers.values()) controller.abort();
    this.controllers.clear();
  }
}