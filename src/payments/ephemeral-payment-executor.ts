import type { ITaskExecutor } from "../interfaces";
import type { Task } from "../models";
import type { CheckoutPaymentSession, StoredPaymentPreference } from "./models";

type RuntimeUpdateSource = ITaskExecutor & {
  onTaskUpdate?: (callback: (task: Task) => void) => () => void;
};

type ProfilePaymentSessionResolver = (
  profileId: string,
  preference?: StoredPaymentPreference
) => CheckoutPaymentSession;

const SESSION_KEY = "__paymentSession";

function sanitizedConfig(config: Task["config"]): Task["config"] {
  const data = { ...(config.data ?? {}) };
  delete data[SESSION_KEY];
  return { ...config, data };
}

function taskProfileId(task: Task): string {
  const data = task.config.data ?? {};
  const action = data["monitorAction"] as { profileId?: string } | undefined;
  return String(data["profileId"] ?? action?.profileId ?? "").trim();
}

export class EphemeralPaymentExecutor implements ITaskExecutor {
  private readonly listeners = new Set<(task: Task) => void>();
  private readonly taskRefs = new Map<string, Task>();
  private readonly unsubscribe?: () => void;

  constructor(
    private readonly delegate: ITaskExecutor,
    private readonly getPaymentSession: (taskId: string) => CheckoutPaymentSession | undefined,
    private readonly getProfilePaymentSession?: ProfilePaymentSessionResolver
  ) {
    const runtimeSource = delegate as RuntimeUpdateSource;
    this.unsubscribe = runtimeSource.onTaskUpdate?.(workerTask => {
      const task = this.taskRefs.get(workerTask.id);
      if (!task) return;
      task.config = sanitizedConfig(workerTask.config);
      task.lastError = workerTask.lastError;
      for (const listener of this.listeners) listener(task);
    });
  }

  onTaskUpdate(callback: (task: Task) => void): () => void {
    this.listeners.add(callback);
    return () => this.listeners.delete(callback);
  }

  async execute(task: Task): Promise<boolean> {
    const session = this.resolveProfilePaymentSession(task, this.getPaymentSession(task.id));
    const workerTask: Task = {
      ...task,
      config: {
        ...task.config,
        data: {
          ...(task.config.data ?? {}),
          ...(session ? { [SESSION_KEY]: session } : {})
        }
      }
    };

    this.taskRefs.set(task.id, task);
    try {
      const success = await this.delegate.execute(workerTask);
      task.config = sanitizedConfig(workerTask.config);
      task.lastError = workerTask.lastError;
      return success;
    } finally {
      this.taskRefs.delete(task.id);
    }
  }

  async updateDiscoveryKeywords(taskId: string, keywords: string[]): Promise<string[]> {
    if (!this.delegate.updateDiscoveryKeywords) {
      throw new Error("Dieser Browser-Executor unterstützt keine Live-Discovery-Keywords.");
    }
    return this.delegate.updateDiscoveryKeywords(taskId, keywords);
  }

  async setFinalPurchaseAllowed(allowed: boolean): Promise<void> {
    await this.delegate.setFinalPurchaseAllowed?.(allowed === true);
  }

  async cancelTask(taskId: string): Promise<void> {
    await this.delegate.cancelTask?.(taskId);
  }

  async close(): Promise<void> {
    this.unsubscribe?.();
    this.listeners.clear();
    this.taskRefs.clear();
    await this.delegate.close?.();
  }

  private resolveProfilePaymentSession(
    task: Task,
    session: CheckoutPaymentSession | undefined
  ): CheckoutPaymentSession | undefined {
    const profileId = taskProfileId(task);

    // A profile-backed task does not need a pre-existing ephemeral card session.
    // If the task has a profile and the vault resolver is available, materialize
    // the card only for the delegated worker copy.
    if (!session) {
      if (!profileId || !this.getProfilePaymentSession) return undefined;
      try {
        return this.getProfilePaymentSession(profileId, { method: "card" });
      } catch {
        // Fail closed. Returning a card-method shell lets payment preparation
        // report missing fields without ever falling back to plaintext task data.
        return { method: "card" };
      }
    }

    if (session.method !== "card") return session;

    const profileOnlySession: CheckoutPaymentSession = {
      method: "card",
      label: session.label
    };

    // Profile-backed card data is authoritative. Any card secret supplied by a
    // legacy/manual task payload is ignored and never gets a fallback path.
    if (!profileId || !this.getProfilePaymentSession) return profileOnlySession;

    try {
      return this.getProfilePaymentSession(profileId, {
        method: "card",
        label: session.label
      });
    } catch {
      // Fail closed: payment preparation reports missing fields instead of using
      // plaintext task payloads when the encrypted profile vault is unavailable.
      return profileOnlySession;
    }
  }
}
