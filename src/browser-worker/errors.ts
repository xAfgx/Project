export class BrowserWorkerError extends Error {
  constructor(message: string, public readonly code: string) {
    super(message);
    this.name = "BrowserWorkerError";
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

export class BrowserContextAlreadyExistsError extends BrowserWorkerError {
  constructor(taskId: string) {
    super(`Browser context already exists for task "${taskId}".`, "CONTEXT_ALREADY_EXISTS");
    this.name = "BrowserContextAlreadyExistsError";
  }
}

export class BrowserProfileInUseError extends BrowserWorkerError {
  constructor(userDataDir: string, ownerId?: string) {
    const owner = ownerId ? ` Owner: ${ownerId}.` : "";
    super(`Browser profile directory "${userDataDir}" is currently active and cannot be reused simultaneously.${owner}`, "PROFILE_IN_USE");
    this.name = "BrowserProfileInUseError";
  }
}

export class BrowserLaunchError extends BrowserWorkerError {
  constructor(taskId: string, cause: unknown) {
    const detail = cause instanceof Error ? cause.message : String(cause);
    super(`Failed to launch browser context for task "${taskId}": ${detail}`, "BROWSER_LAUNCH_FAILED");
    this.name = "BrowserLaunchError";
  }
}

export class BrowserWorkerStateError extends BrowserWorkerError {
  constructor(state: string) {
    super(`Browser worker cannot accept new contexts while state="${state}".`, "WORKER_NOT_READY");
    this.name = "BrowserWorkerStateError";
  }
}
