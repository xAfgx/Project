import {
  ITaskExecutor,
  ITaskRepository,
  IBrowserManager,
  IShopAdapter,
  IProxyManager,
  IWorker,
  TaskEvents
} from "../interfaces";
import { CancellationManager } from "../cancellation-manager";
import { EventBus } from "../event-bus";
import { RetryScheduler } from "../retry-scheduler";
import { StateMachine } from "../state-machine";
import { TaskRegistry } from "../task-registry";
import { Task, TaskConfig, TaskState } from "../models";
import { TaskExecutor } from "../task-executor";
import { WorkerPool } from "../worker-pool";
import { isEarlyGateChildTask, setEarlyGateRuntime } from "../monitor/early-gate";

type RuntimeUpdateSource = ITaskExecutor & {
  onTaskUpdate?: (callback: (task: Task) => void) => () => void;
};

const TRANSIENT_RUNTIME_KEYS = [
  "queueStatus",
  "retryPolicy",
  "checkoutPreparation",
  "paymentPreparation",
  "shopifyFlow",
  "finalPurchaseRuntime",
  "liveChallengeStatus",
  "liveChallengeType",
  "captchaStatus",
  "challengeStatus",
  "captchaType",
  "challengeType",
  "proxyRuntime",
  "earlyGateRuntime",
  "autoCheckoutRuntime",
  "earlyGateFlow"
] as const;

export class TaskOrchestrator {
  private readonly eventBus = new EventBus();
  private readonly stateMachine = new StateMachine();
  private readonly retryScheduler = new RetryScheduler(this.eventBus);
  private readonly cancellationManager = new CancellationManager();
  private readonly workerPool = new WorkerPool(this.eventBus);
  private readonly registry: TaskRegistry;
  private readonly executor: ITaskExecutor;
  private readonly repository: ITaskRepository;
  private readonly pendingTaskIds: string[] = [];
  private readonly pendingTaskIdSet = new Set<string>();
  private readonly pausedRunningTaskIds = new Set<string>();
  private readonly unsubscribeExecutorUpdates?: () => void;

  constructor(
    repository: ITaskRepository,
    executor: ITaskExecutor,
    browserManager?: IBrowserManager,
    shopAdapter?: IShopAdapter,
    proxyManager?: IProxyManager
  ) {
    this.registry = new TaskRegistry(repository);
    this.repository = repository;

    this.executor =
      browserManager && shopAdapter && proxyManager
        ? new TaskExecutor(
            this.eventBus,
            this.cancellationManager,
            browserManager,
            shopAdapter,
            proxyManager
          )
        : executor;

    const runtimeSource = this.executor as RuntimeUpdateSource;
    this.unsubscribeExecutorUpdates = runtimeSource.onTaskUpdate?.(task => {
      this.handleRuntimeTaskUpdate(task);
    });

    this.eventBus.on("taskFailed", task => {
      const retryPolicy = task.config.data?.["retryPolicy"] as Record<string, unknown> | undefined;
      if (retryPolicy?.["blocked"] === true) return;
      if (task.retries < task.maxRetries) {
        task.retries += 1;
        this.transition(task, TaskState.RETRYING);
        this.retryScheduler.scheduleRetry(task);
      }
    });

    this.eventBus.on("taskRetrying", task => {
      if (task.state === TaskState.RETRYING) {
        this.transition(task, TaskState.QUEUED);
        void this.startTask(task.id);
      }
    });
  }

  async initialize(): Promise<void> {
    const restored = await this.registry.loadAllTasks();

    for (const task of restored) {
      if (task.state === TaskState.CREATED) {
        this.transition(task, TaskState.QUEUED);
        await this.registry.saveTask(task.id);
        continue;
      }

      if (this.isRecoverableActiveState(task.state)) {
        task.lastError = task.lastError || "Nach App-Neustart sicher pausiert. Fortsetzen erforderlich.";
        this.transition(task, TaskState.PAUSED);
        await this.registry.saveTask(task.id);
      }
    }
  }

  createTask(config: TaskConfig): Task {
    const task = this.registry.createTask(config);
    this.eventBus.emit("taskCreated", task);
    this.transition(task, TaskState.QUEUED);
    return task;
  }

  async startTask(taskId: string): Promise<void> {
    const task = this.registry.getTask(taskId);
    if (!task) throw new Error(`Task ${taskId} not found`);
    if (task.state !== TaskState.QUEUED) {
      throw new Error(`Task ${taskId} cannot start from ${task.state}`);
    }

    const workerId = this.workerPool.assignTask(task);
    if (!workerId) {
      this.enqueueTask(taskId);
      return;
    }

    this.removePendingTask(taskId);

    try {
      this.transition(task, TaskState.STARTING);
      this.transition(task, TaskState.RUNNING);
      this.eventBus.emit("taskStarted", task);

      let success: boolean;
      try {
        const result = await this.executor.execute(task);
        success = typeof result === "boolean" ? result : result.success;
      } catch (error) {
        const wasPausedWhileRunning = this.pausedRunningTaskIds.delete(task.id);
        const currentState = task.state as TaskState;
        task.lastError = error instanceof Error ? error.message : String(error);

        if (currentState === TaskState.CANCELLED || currentState === TaskState.PAUSED || wasPausedWhileRunning) {
          if (wasPausedWhileRunning && currentState === TaskState.QUEUED) {
            this.enqueueTask(task.id);
          }
          await this.registry.saveTask(task.id);
          return;
        }

        if (this.stateMachine.canTransition(task.state, TaskState.FAILED)) {
          this.transition(task, TaskState.FAILED);
          this.eventBus.emit("taskFailed", task);
        }
        await this.registry.saveTask(task.id);
        return;
      }

      const wasPausedWhileRunning = this.pausedRunningTaskIds.delete(task.id);
      const currentState = task.state as TaskState;
      if (currentState === TaskState.CANCELLED || currentState === TaskState.PAUSED || wasPausedWhileRunning) {
        if (wasPausedWhileRunning && currentState === TaskState.QUEUED) {
          this.enqueueTask(task.id);
        }
        await this.registry.saveTask(task.id);
        return;
      }

      if (success) {
        this.completeSuccessfulTask(task);
      } else {
        this.transition(task, TaskState.FAILED);
        this.eventBus.emit("taskFailed", task);
      }

      await this.registry.saveTask(task.id);
    } finally {
      this.workerPool.releaseWorker(workerId);
      this.drainQueue();
    }
  }

  async pauseTask(taskId: string): Promise<void> {
    const task = this.registry.getTask(taskId);
    if (!task) throw new Error(`Task ${taskId} not found`);

    this.removePendingTask(taskId);
    this.retryScheduler.cancelRetry(taskId);

    if (!this.stateMachine.canTransition(task.state, TaskState.PAUSED)) {
      throw new Error(`Task ${taskId} cannot pause from ${task.state}`);
    }

    if (this.isRunningLike(task.state)) {
      this.pausedRunningTaskIds.add(taskId);
    }

    this.transition(task, TaskState.PAUSED);
    this.cancellationManager.cancelTask(taskId);
    void this.executor.cancelTask?.(taskId).catch(error => {
      task.lastError = error instanceof Error ? error.message : String(error);
    });

    await this.registry.saveTask(task.id);
    this.drainQueue();
  }

  async resumeTask(taskId: string): Promise<void> {
    const task = this.registry.getTask(taskId);
    if (!task) throw new Error(`Task ${taskId} not found`);
    if (task.state !== TaskState.PAUSED) {
      throw new Error(`Task ${taskId} cannot resume from ${task.state}`);
    }

    this.transition(task, TaskState.QUEUED);
    this.eventBus.emit("taskResumed", task);
    await this.registry.saveTask(task.id);
    this.enqueueTask(task.id);
    this.drainQueue();
  }

  cancelTask(taskId: string): void {
    const task = this.registry.getTask(taskId);
    if (!task) throw new Error(`Task ${taskId} not found`);

    this.removePendingTask(taskId);
    this.pausedRunningTaskIds.delete(taskId);
    this.retryScheduler.cancelRetry(taskId);
    this.cancellationManager.cancelTask(taskId);
    void this.executor.cancelTask?.(taskId).catch(error => {
      task.lastError = error instanceof Error ? error.message : String(error);
    });

    if (this.stateMachine.canTransition(task.state, TaskState.CANCELLED)) {
      this.transition(task, TaskState.CANCELLED);
      this.eventBus.emit("taskCancelled", task);
    }
  }

  setTaskQueueWaiting(taskId: string, waiting: boolean): void {
    const task = this.registry.getTask(taskId);
    if (!task) return;
    const queueStatus = task.config.data?.["queueStatus"] as Record<string, unknown> | undefined;

    if (waiting && task.state === TaskState.RUNNING) {
      if (isEarlyGateChildTask(task)) {
        setEarlyGateRuntime(task, {
          activeArea: "browser-child",
          queueEnteredAt: String(queueStatus?.["detectedAt"] ?? new Date().toISOString())
        });
      }
      this.transition(task, TaskState.WAITING_QUEUE);
      return;
    }

    if (!waiting && task.state === TaskState.WAITING_QUEUE) {
      const released = queueStatus?.["phase"] === "released";
      if (isEarlyGateChildTask(task) && released) {
        setEarlyGateRuntime(task, {
          activeArea: "browser-child",
          stage: "post-queue-discovery",
          queueReleasedAt: String(queueStatus?.["releasedAt"] ?? queueStatus?.["updatedAt"] ?? new Date().toISOString()),
          postQueueDiscoveryAt: String(queueStatus?.["releasedAt"] ?? queueStatus?.["updatedAt"] ?? new Date().toISOString())
        });
        this.transition(task, TaskState.POST_QUEUE_DISCOVERY);
      } else if (!isEarlyGateChildTask(task) && queueStatus?.["phase"] !== "timed-out") {
        this.transition(task, TaskState.RUNNING);
      }
    }
  }

  addWorker(worker: IWorker): void {
    this.workerPool.addWorker(worker);
    this.drainQueue();
  }

  getAvailableWorkers(): number {
    return this.workerPool.getAvailableWorkers();
  }

  getTask(id: string): Task | undefined {
    return this.registry.getTask(id);
  }

  getAllTasks(): Task[] {
    return this.registry.getAllTasks();
  }

  async deleteTask(taskId: string): Promise<void> {
    const task = this.registry.getTask(taskId);
    if (!task) throw new Error(`Task ${taskId} not found`);

    this.removePendingTask(taskId);
    this.pausedRunningTaskIds.delete(taskId);
    this.retryScheduler.cancelRetry(taskId);
    this.cancellationManager.cancelTask(taskId);
    void this.executor.cancelTask?.(taskId).catch(() => undefined);

    this.registry.deleteTask(taskId);
    await this.repository.delete(taskId);
    this.eventBus.emit("taskDeleted", taskId);
  }

  async restartTask(taskId: string): Promise<void> {
    const task = this.registry.getTask(taskId);
    if (!task) throw new Error(`Task ${taskId} not found`);

    this.removePendingTask(taskId);
    this.pausedRunningTaskIds.delete(taskId);
    this.retryScheduler.cancelRetry(taskId);
    this.cancellationManager.cancelTask(taskId);
    void this.executor.cancelTask?.(taskId).catch(() => undefined);

    task.state = TaskState.QUEUED;
    task.retries = 0;
    task.lastError = undefined;
    task.updatedAt = new Date();
    this.clearTransientRuntime(task);

    await this.registry.saveTask(task.id);
    this.eventBus.emit("taskQueued", task);
    this.enqueueTask(task.id);
    this.drainQueue();
  }

  on<T extends keyof TaskEvents>(
    event: T,
    callback: (data: TaskEvents[T]) => void
  ): () => void {
    return this.eventBus.on(event, callback);
  }

  cleanup(): void {
    this.pendingTaskIds.length = 0;
    this.pendingTaskIdSet.clear();
    this.pausedRunningTaskIds.clear();
    this.unsubscribeExecutorUpdates?.();
    this.retryScheduler.cleanup();
    this.cancellationManager.cleanup();
    for (const worker of this.workerPool.getAllWorkers()) worker.stop();
  }

  private handleRuntimeTaskUpdate(task: Task): void {
    const current = this.registry.getTask(task.id);
    if (!current) return;

    const before = current.state;
    const queueStatus = current.config.data?.["queueStatus"] as Record<string, unknown> | undefined;
    const waiting = Boolean(queueStatus?.["active"]);
    this.setTaskQueueWaiting(current.id, waiting);

    if (!waiting) this.applyEarlyGateFlowState(current);

    if (current.state === before) {
      current.updatedAt = new Date();
      this.eventBus.emit("taskUpdated", current);
    }
  }

  private applyEarlyGateFlowState(task: Task): void {
    if (!isEarlyGateChildTask(task)) return;
    const flow = task.config.data?.["earlyGateFlow"] as Record<string, unknown> | undefined;
    const stage = String(flow?.["stage"] ?? "");
    const desired =
      stage === "post-queue-discovery" ? TaskState.POST_QUEUE_DISCOVERY :
      stage === "product-found" ? TaskState.PRODUCT_FOUND :
      stage === "cart" ? TaskState.CART :
      stage === "checkout" ? TaskState.CHECKOUT :
      undefined;

    if (!desired || task.state === desired) return;
    if (this.stateMachine.canTransition(task.state, desired)) {
      this.transition(task, desired);
    }
  }

  private completeSuccessfulTask(task: Task): void {
    if (task.state === TaskState.CHECKOUT) {
      this.transition(task, TaskState.SUCCESS);
      return;
    }

    if (this.stateMachine.canTransition(task.state, TaskState.CHECKOUT)) {
      this.transition(task, TaskState.CHECKOUT);
      this.transition(task, TaskState.SUCCESS);
      return;
    }

    if (this.stateMachine.canTransition(task.state, TaskState.SUCCESS)) {
      this.transition(task, TaskState.SUCCESS);
      return;
    }

    throw new Error(`Task ${task.id} kann aus ${task.state} nicht erfolgreich abgeschlossen werden.`);
  }

  private enqueueTask(taskId: string): void {
    if (this.pendingTaskIdSet.has(taskId)) return;
    this.pendingTaskIdSet.add(taskId);
    this.pendingTaskIds.push(taskId);
  }

  private removePendingTask(taskId: string): void {
    if (!this.pendingTaskIdSet.delete(taskId)) return;

    const index = this.pendingTaskIds.indexOf(taskId);
    if (index >= 0) this.pendingTaskIds.splice(index, 1);
  }

  private clearTransientRuntime(task: Task): void {
    const data = task.config.data;
    if (!data) return;
    for (const key of TRANSIENT_RUNTIME_KEYS) {
      delete data[key];
    }
  }

  private drainQueue(): void {
    // Inspect each task that was pending when this drain started at most once.
    // A resumed task may still own its previous worker while that executor is
    // unwinding; keep it queued until releaseWorker() triggers the next drain.
    const candidates = this.pendingTaskIds.length;
    for (
      let inspected = 0;
      inspected < candidates && this.workerPool.getAvailableWorkers() > 0 && this.pendingTaskIds.length > 0;
      inspected += 1
    ) {
      const taskId = this.pendingTaskIds.shift();
      if (!taskId) return;

      this.pendingTaskIdSet.delete(taskId);
      const task = this.registry.getTask(taskId);
      if (!task || task.state !== TaskState.QUEUED) continue;

      if (this.workerPool.hasAssignment(taskId)) {
        this.enqueueTask(taskId);
        continue;
      }

      void this.startTask(taskId).catch(error => {
        task.lastError = error instanceof Error ? error.message : String(error);
      });
    }
  }

  private isRunningLike(state: TaskState): boolean {
    return [
      TaskState.STARTING,
      TaskState.RUNNING,
      TaskState.WAITING_QUEUE,
      TaskState.POST_QUEUE_DISCOVERY,
      TaskState.PRODUCT_FOUND,
      TaskState.CART,
      TaskState.CHECKOUT
    ].includes(state);
  }

  private isRecoverableActiveState(state: TaskState): boolean {
    return this.isRunningLike(state) || state === TaskState.RETRYING;
  }

  private transition(task: Task, newState: TaskState): void {
    if (!this.stateMachine.canTransition(task.state, newState)) {
      throw new Error(`Invalid transition: ${task.state} -> ${newState}`);
    }

    const previousState = task.state;
    task.state = newState;
    task.updatedAt = new Date();

    this.eventBus.emit("taskStateChanged", {
      task,
      previousState,
      newState
    });

    const event =
      newState === TaskState.QUEUED ? "taskQueued" :
      newState === TaskState.SUCCESS ? "taskCompleted" :
      newState === TaskState.CANCELLED ? "taskCancelled" :
      newState === TaskState.PAUSED ? "taskPaused" :
      "taskUpdated";

    this.eventBus.emit(event, task);
  }
}
