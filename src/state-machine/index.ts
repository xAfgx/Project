import { TaskState } from "../models";

export class StateMachine {
  private readonly transitions = new Map<TaskState, Set<TaskState>>([
    [TaskState.CREATED, new Set([TaskState.QUEUED])],
    [TaskState.QUEUED, new Set([TaskState.STARTING, TaskState.CANCELLED, TaskState.PAUSED])],
    [TaskState.STARTING, new Set([TaskState.RUNNING, TaskState.FAILED, TaskState.CANCELLED, TaskState.PAUSED])],
    [TaskState.RUNNING, new Set([
      TaskState.WAITING_QUEUE,
      TaskState.POST_QUEUE_DISCOVERY,
      TaskState.PRODUCT_FOUND,
      TaskState.CART,
      TaskState.CHECKOUT,
      TaskState.SUCCESS,
      TaskState.FAILED,
      TaskState.CANCELLED,
      TaskState.PAUSED
    ])],
    [TaskState.WAITING_QUEUE, new Set([
      TaskState.RUNNING,
      TaskState.POST_QUEUE_DISCOVERY,
      TaskState.FAILED,
      TaskState.CANCELLED,
      TaskState.PAUSED
    ])],
    [TaskState.POST_QUEUE_DISCOVERY, new Set([
      TaskState.PRODUCT_FOUND,
      TaskState.FAILED,
      TaskState.CANCELLED,
      TaskState.PAUSED
    ])],
    [TaskState.PAUSED, new Set([TaskState.QUEUED, TaskState.CANCELLED])],
    [TaskState.PRODUCT_FOUND, new Set([TaskState.CART, TaskState.FAILED, TaskState.CANCELLED, TaskState.PAUSED])],
    [TaskState.CART, new Set([TaskState.CHECKOUT, TaskState.FAILED, TaskState.CANCELLED, TaskState.PAUSED])],
    [TaskState.CHECKOUT, new Set([
      TaskState.SUCCESS,
      TaskState.RETRYING,
      TaskState.FAILED,
      TaskState.CANCELLED,
      TaskState.PAUSED
    ])],
    [TaskState.RETRYING, new Set([TaskState.QUEUED, TaskState.RUNNING, TaskState.PAUSED])],
    [TaskState.SUCCESS, new Set()],
    [TaskState.FAILED, new Set([TaskState.RETRYING])],
    [TaskState.CANCELLED, new Set()]
  ]);

  canTransition(from: TaskState, to: TaskState): boolean {
    return this.transitions.get(from)?.has(to) ?? false;
  }

  getAllowedTransitions(from: TaskState): TaskState[] {
    return [...(this.transitions.get(from) ?? [])];
  }
}
