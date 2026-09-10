import { EventBus } from "../event-bus";
import { IWorker } from "../interfaces";
import { Task } from "../models";

export class WorkerPool {
  private readonly workers = new Map<string, IWorker>();
  private readonly available: string[] = [];
  private readonly assignments = new Map<string, string>();

  constructor(private readonly eventBus: EventBus) {}

  addWorker(worker: IWorker): void {
    if (this.workers.has(worker.id)) {
      throw new Error(`Worker ${worker.id} already exists`);
    }

    this.workers.set(worker.id, worker);
    this.available.push(worker.id);
    this.eventBus.emit("workerAdded", worker);
  }

  assignTask(task: Task): string | null {
    // A paused/resumed task can be queued while its previous executor call is
    // still unwinding. Never allow the same task id to own two workers at once.
    if (this.assignments.has(task.id)) return null;

    const workerId = this.available.shift();
    if (!workerId) return null;

    const worker = this.workers.get(workerId);
    if (!worker) return null;

    worker.status = "busy";
    worker.currentTaskId = task.id;
    this.assignments.set(task.id, workerId);
    this.eventBus.emit("workerAssigned", { taskId: task.id, workerId });

    return workerId;
  }

  releaseWorker(workerId: string): void {
    const worker = this.workers.get(workerId);
    if (!worker) return;

    const assignment = [...this.assignments.entries()]
      .find(([, id]) => id === workerId);

    if (assignment) {
      this.assignments.delete(assignment[0]);
      this.eventBus.emit("workerReleased", {
        taskId: assignment[0],
        workerId
      });
    }

    worker.status = "idle";
    worker.currentTaskId = undefined;

    if (!this.available.includes(workerId)) {
      this.available.push(workerId);
    }
  }

  hasAssignment(taskId: string): boolean {
    return this.assignments.has(taskId);
  }

  getAvailableWorkers(): number {
    return this.available.length;
  }

  getAllWorkers(): IWorker[] {
    return [...this.workers.values()];
  }
}
