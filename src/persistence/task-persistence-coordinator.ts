import { ITaskPersistenceRepository } from "../interfaces";
import { Task, TaskLogEntry, TaskLogLevel, TaskState } from "../models";
import { TaskOrchestrator } from "../orchestrator";

function cloneTask(task: Task): Task {
  return {
    ...task,
    config: JSON.parse(JSON.stringify(task.config)) as Task["config"],
    createdAt: new Date(task.createdAt),
    updatedAt: new Date(task.updatedAt)
  };
}

function levelForState(state: TaskState): TaskLogLevel {
  if (state === TaskState.FAILED) return "error";
  if ([TaskState.PAUSED, TaskState.CANCELLED, TaskState.RETRYING].includes(state)) return "warn";
  return "info";
}

function stateMessage(task: Task, previousState: TaskState, newState: TaskState): string {
  if (newState === TaskState.FAILED && task.lastError) {
    return `${previousState} -> ${newState}: ${task.lastError}`;
  }
  return `${previousState} -> ${newState}`;
}

export class TaskPersistenceCoordinator {
  private readonly unsubscribers: Array<() => void> = [];
  private writeQueue: Promise<void> = Promise.resolve();
  private lastError?: string;

  constructor(
    orchestrator: TaskOrchestrator,
    private readonly repository: ITaskPersistenceRepository
  ) {
    this.unsubscribers.push(
      orchestrator.on("taskCreated", task => {
        const snapshot = cloneTask(task);
        this.enqueue(snapshot, {
          taskId: snapshot.id,
          event: "taskCreated",
          state: snapshot.state,
          level: "info",
          message: "Task erstellt",
          createdAt: new Date()
        });
      })
    );

    this.unsubscribers.push(
      orchestrator.on("taskStateChanged", ({ task, previousState, newState }) => {
        const snapshot = cloneTask(task);
        this.enqueue(snapshot, {
          taskId: snapshot.id,
          event: "taskStateChanged",
          state: newState,
          level: levelForState(newState),
          message: stateMessage(snapshot, previousState, newState),
          createdAt: new Date()
        });
      })
    );
  }

  getLastError(): string | undefined {
    return this.lastError;
  }

  async flush(): Promise<void> {
    await this.writeQueue;
  }

  async close(): Promise<void> {
    for (const unsubscribe of this.unsubscribers.splice(0)) unsubscribe();
    await this.flush();
  }

  private enqueue(task: Task, entry: TaskLogEntry): void {
    const run = this.writeQueue.then(async () => {
      await this.repository.recordTaskEvent(task, entry);
      this.lastError = undefined;
    });

    this.writeQueue = run.catch(error => {
      this.lastError = error instanceof Error ? error.message : String(error);
    });
  }
}
