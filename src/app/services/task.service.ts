import { Injectable } from "@angular/core";
import { ElectronService } from "./electron.service";

@Injectable({ providedIn: "root" })
export class TaskService {
  constructor(private readonly electron: ElectronService) {}

  createTask(config: unknown) {
    return this.electron.createTask(config);
  }

  startTask(taskId: string) {
    return this.electron.startTask(taskId);
  }

  stopTask(taskId: string) {
    return this.electron.stopTask(taskId);
  }

  getTasks() {
    return this.electron.getTaskList();
  }
}
