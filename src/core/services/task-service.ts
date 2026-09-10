import { Task, TaskConfig, TaskState } from '../models';

export class TaskService {
  private tasks: Map<string, Task> = new Map();

  createTask(config: TaskConfig): Task {
    const task: Task = {
      id: config.id,
      config,
      state: TaskState.CREATED,
      createdAt: new Date(),
      updatedAt: new Date(),
      retries: 0,
      maxRetries: config.maxRetries ?? 3,
      shopId: config.shopId
    };

    this.tasks.set(task.id, task);
    return task;
  }

  getTask(id: string): Task | undefined {
    return this.tasks.get(id);
  }

  getAllTasks(): Task[] {
    return Array.from(this.tasks.values());
  }

  updateTask(id: string, updates: Partial<Task>): void {
    const task = this.tasks.get(id);
    if (task) {
      Object.assign(task, updates, { updatedAt: new Date() });
    }
  }

  deleteTask(id: string): boolean {
    return this.tasks.delete(id);
  }

  // Zustandsübergänge
  transitionToQueued(taskId: string): void {
    const task = this.getTask(taskId);
    if (task && task.state === TaskState.CREATED) {
      task.state = TaskState.QUEUED;
      task.updatedAt = new Date();
    }
  }

  transitionToRunning(taskId: string): void {
    const task = this.getTask(taskId);
    if (task && task.state === TaskState.QUEUED) {
      task.state = TaskState.RUNNING;
      task.updatedAt = new Date();
    }
  }

  transitionToSuccess(taskId: string): void {
    const task = this.getTask(taskId);
    if (task && task.state === TaskState.CHECKOUT) {
      task.state = TaskState.SUCCESS;
      task.updatedAt = new Date();
    }
  }

  transitionToFailed(taskId: string, error?: string): void {
    const task = this.getTask(taskId);
    if (task) {
      task.state = TaskState.FAILED;
      task.lastError = error;
      task.updatedAt = new Date();
    }
  }
}
