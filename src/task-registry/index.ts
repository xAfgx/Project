import { ITaskRepository } from "../interfaces";
import { Task, TaskConfig, TaskState } from "../models";

export class TaskRegistry {
  private readonly tasks = new Map<string, Task>();

  constructor(private readonly repository: ITaskRepository) {}

  createTask(config: TaskConfig): Task {
    if (this.tasks.has(config.id)) {
      throw new Error(`Task ${config.id} already exists`);
    }

    const now = new Date();
    const task: Task = {
      id: config.id,
      config,
      state: TaskState.CREATED,
      createdAt: now,
      updatedAt: now,
      retries: 0,
      maxRetries: config.maxRetries ?? 3
    };

    this.tasks.set(task.id, task);
    return task;
  }

  getTask(id: string): Task | undefined {
    return this.tasks.get(id);
  }

  getAllTasks(): Task[] {
    return [...this.tasks.values()];
  }

  updateTask(id: string, updates: Partial<Task>): Task {
    const task = this.tasks.get(id);
    if (!task) throw new Error(`Task ${id} not found`);
    Object.assign(task, updates, { updatedAt: new Date() });
    return task;
  }

  deleteTask(id: string): boolean {
    return this.tasks.delete(id);
  }

  async saveTask(id: string): Promise<void> {
    const task = this.getTask(id);
    if (task) await this.repository.save(task);
  }

  async loadTask(id: string): Promise<Task | null> {
    const task = await this.repository.findById(id);
    if (task) this.tasks.set(id, task);
    return task;
  }

  async loadAllTasks(): Promise<Task[]> {
    const tasks = await this.repository.findAll();
    this.tasks.clear();
    for (const task of tasks) this.tasks.set(task.id, task);
    return tasks;
  }
}
