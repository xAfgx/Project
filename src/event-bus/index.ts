import { TaskEvents } from "../interfaces";

type Listener<T> = (data: T) => void;

export class EventBus {
  private readonly listeners = new Map<keyof TaskEvents, Set<Listener<unknown>>>();

  on<T extends keyof TaskEvents>(
    event: T,
    callback: Listener<TaskEvents[T]>
  ): () => void {
    let set = this.listeners.get(event);
    if (!set) {
      set = new Set();
      this.listeners.set(event, set);
    }

    const listener: Listener<unknown> = callback as Listener<unknown>;
    set.add(listener);

    return () => this.off(event, callback);
  }

  off<T extends keyof TaskEvents>(
    event: T,
    callback: Listener<TaskEvents[T]>
  ): void {
    this.listeners.get(event)?.delete(callback as Listener<unknown>);
  }

  emit<T extends keyof TaskEvents>(event: T, data: TaskEvents[T]): void {
    for (const listener of this.listeners.get(event) ?? []) {
      listener(data);
    }
  }
}