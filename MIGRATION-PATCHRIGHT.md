# ARES Browser Worker Migration

## Zielarchitektur

```text
Electron / Angular
        |
        v
TaskOrchestrator
        |
        v
BrowserWorkerPoolClient
        |  JSON-lines RPC over stdio
        v
External Node.js 20+ worker process(es)
        |
        v
PatchrightBrowserWorker
        |
        v
chromium.launchPersistentContext(...)
        |
        +-- Task A -> Chrome profile/context A -> proxy A
        +-- Task B -> Chrome profile/context B -> proxy B
        +-- Task C -> Chrome profile/context C -> proxy C
```

Patchright is not imported by Electron. The child process checks `process.versions.node` before loading Patchright, which prevents Electron 21's embedded Node 16 runtime from triggering Patchright's Node 20+ requirement.

## Browser lifecycle

`src/browser-worker/browser-worker.ts` is the browser-core contract:

- `createContext(config)` creates one task-owned persistent Chrome context.
- `closeContext(taskId)` is idempotent and closes that task's live context.
- `health()` reports active contexts, pending creations and worker state.

`src/browser-worker/patchright-launcher.ts` is the only Patchright launch boundary. It always uses:

```ts
chromium.launchPersistentContext(userDataDir, {
  channel: "chrome",
  proxy,
  // ...
});
```

Each active task has a unique `userDataDir`. The profile directory is persistent across relaunches of the same task ID, while no two live contexts may own the same task ID simultaneously.

## Proxies

Profile proxy settings are converted into Patchright's launch proxy object and applied to the task-owned persistent context. Credentials stay inside the external worker request/runtime and are not exposed to the Angular renderer.

## UI interaction helper

`src/browser-worker/ui-interaction-helper.ts` uses `ghost-cursor` only for its 2D Bézier `path()` generator. Patchright's native `page.mouse` dispatches the actual UI test mouse movements and clicks. No Puppeteer browser integration is present.

## Worker process pool

Default: one external Node process, because a single worker process can already host several isolated task contexts concurrently.

Optional process-level fault isolation:

```bat
set ARES_BROWSER_WORKER_PROCESSES=2
```

Maximum configured process count is capped at 4. The Electron-side pool assigns new tasks to the least-loaded process and remembers task ownership so cancellation reaches the correct process.

Task concurrency is separately controlled by:

```bat
set ARES_MAX_CONCURRENT_TASKS=4
```

## Node executable

By default the worker starts `node` from PATH. To pin a specific Node 20+/22+/24 executable:

```bat
set ARES_NODE_EXECUTABLE=C:\Program Files\nodejs\node.exe
```

This is the recommended production setting if ARES is launched outside a developer shell.

## Verification

```bat
node -v
npm install
npm run browser:install
npm run build:backend
npm run build:ui
npm test -- --runInBand
npm run electron
```

The external browser-worker runtime must be Node.js 20 or higher. Electron itself can use an older embedded Node runtime because it does not import Patchright.
