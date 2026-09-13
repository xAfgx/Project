const fs = require("fs");
const os = require("os");
const path = require("path");
const { performance } = require("perf_hooks");

const repoRoot = path.resolve(__dirname, "..");
const hookPath = path.join(repoRoot, "tools", "runtime-timing-hook.js");
const logDir = path.join(repoRoot, "runtime-probe-logs");
fs.mkdirSync(logDir, { recursive: true });

const suiteId = new Date().toISOString().replace(/[:.]/g, "-");
const timingLog = path.join(logDir, `semantic-checkout-${suiteId}.jsonl`);
const summaryPath = path.join(logDir, `semantic-checkout-${suiteId}.summary.json`);
const headless = process.env.ARES_PROBE_HEADLESS !== "0";

process.env.ARES_TIMING_LOG = timingLog;
process.env.ARES_CAPTCHA_MODE = "siglip";
process.env.NODE_OPTIONS = `${process.env.NODE_OPTIONS ? `${process.env.NODE_OPTIONS} ` : ""}--require=${hookPath}`;

require(hookPath);

const { TaskOrchestrator } = require("../dist/backend/orchestrator");
const { TaskRepositoryMock, WorkerMock } = require("../dist/backend/mocks");
const { BrowserWorkerPoolClient } = require("../dist/backend/browser-worker/client");
const { TaskState } = require("../dist/backend/models");

function append(event) {
  const payload = {
    timestamp: new Date().toISOString(),
    elapsedMs: Math.round(performance.now() * 1000) / 1000,
    pid: process.pid,
    ...event
  };
  fs.appendFileSync(timingLog, `${JSON.stringify(payload)}\n`, "utf8");
  if (event.source === "parent" && /^(probe\.run|probe\.suite|probe\.worker|orchestrator\.state|worker\.assigned|worker\.released)/.test(event.action || "")) {
    const parts = [
      new Date().toLocaleTimeString(),
      event.action,
      event.runLabel ? `run=${event.runLabel}` : "",
      event.taskId ? `task=${event.taskId}` : "",
      event.previousState && event.state ? `${event.previousState}->${event.state}` : event.state ? `state=${event.state}` : "",
      event.result ? `result=${event.result}` : "",
      event.error ? `error=${event.error}` : ""
    ].filter(Boolean);
    process.stderr.write(`${parts.join(" ")}\n`);
  }
}

function wait(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function makeProfile() {
  return {
    id: "default-profile",
    name: "Default Runtime Probe Profile",
    contact: {
      firstName: "Max",
      lastName: "Mustermann",
      email: "max.mustermann@example.test",
      phone: "+4915112345678"
    },
    address: {
      address1: "Musterstrasse 12",
      address2: "",
      street: "Musterstrasse",
      houseNumber: "12",
      postalCode: "10115",
      city: "Berlin",
      countryCode: "DE"
    },
    browser: {
      headless,
      kiAutofill: true
    }
  };
}

function makeShop(baseUrl) {
  return {
    id: "pokemon-center-offline",
    name: "Pokemon Center Offline Runtime Flow",
    baseUrl,
    platform: "pokemon-center",
    config: {}
  };
}

function makeTaskConfig(runLabel, index, options = {}) {
  return {
    id: `runtime-probe-${suiteId}-${index}`,
    name: `Runtime Probe ${runLabel}`,
    shopId: "pokemon-center-offline",
    maxRetries: 0,
    data: {
      profileId: "default-profile",
      monitorStrategy: {
        mode: "early-gate",
        productName: "Wald Puzzle",
        discoveryKeywords: ["Wald Puzzle", "Waldszene Puzzle", "Waldszene Puzzle 500 Teile", "Puzzle"]
      },
      postQueueDiscovery: {
        productName: "Wald Puzzle",
        keywords: ["Wald Puzzle", "Waldszene Puzzle", "Waldszene Puzzle 500 Teile", "Puzzle"]
      },
      // Test-only card data so the payment preparer can be exercised. The final
      // purchase stays disabled; nothing is ever submitted.
      __paymentSession: {
        method: "card",
        card: {
          holderName: "Max Mustermann",
          cardNumber: "4111111111111111",
          expiry: "12/30",
          securityCode: "123"
        }
      },
      postQueueEntryUrl: "/de-de",
      browserConfig: {
        headless,
        queueMaxWaitMs: 120000
      },
      discoveryMaxMs: 120000,
      checkoutFieldLoadMaxMs: 120000,
      checkoutPreparationMaxMs: 120000,
      orderConfirmationAttempts: 2,
      orderConfirmationRetryDelayMs: 750,
      probeRunLabel: runLabel,
      ...options.data
    }
  };
}

function makeRuntime(baseUrl, runtimeLabel) {
  const shop = makeShop(baseUrl);
  const profile = makeProfile();
  const profileRoot = path.join(os.tmpdir(), `ares-runtime-probe-${suiteId}-${runtimeLabel}`);
  const browserWorker = new BrowserWorkerPoolClient(
    shopId => shopId === shop.id ? shop : undefined,
    profileId => profileId === profile.id ? profile : undefined,
    {
      processCount: 1,
      profileRoot,
      requestTimeoutMs: 120000,
      executeTimeoutMs: 600000,
      heartbeatIntervalMs: 10000,
      heartbeatTimeoutMs: 10000,
      onTaskUpdate: task => append({
        source: "parent",
        action: "worker.task-update",
        taskId: task.id,
        state: task.state,
        result: task.lastError ? "error" : "ok",
        checkoutPreparation: task.config?.data?.checkoutPreparation,
        earlyGateFlow: task.config?.data?.earlyGateFlow
      })
    }
  );
  const repo = new TaskRepositoryMock();
  const orchestrator = new TaskOrchestrator(repo, browserWorker);
  orchestrator.addWorker(new WorkerMock(`probe-slot-${runtimeLabel}`));

  for (const event of ["taskCreated", "taskQueued", "taskStarted", "taskUpdated", "taskCompleted", "taskFailed", "taskCancelled", "taskRetrying"]) {
    orchestrator.on(event, task => append({
      source: "parent",
      action: `orchestrator.${event}`,
      taskId: task.id,
      state: task.state,
      result: task.lastError ? "error" : "ok",
      error: task.lastError
    }));
  }
  orchestrator.on("taskStateChanged", payload => append({
    source: "parent",
    action: "orchestrator.state",
    taskId: payload.task.id,
    state: payload.newState,
    previousState: payload.previousState,
    result: "ok"
  }));
  orchestrator.on("workerAssigned", payload => append({
    source: "parent",
    action: "worker.assigned",
    taskId: payload.taskId,
    worker: payload.workerId,
    result: "ok"
  }));
  orchestrator.on("workerReleased", payload => append({
    source: "parent",
    action: "worker.released",
    taskId: payload.taskId,
    worker: payload.workerId,
    result: "ok"
  }));

  return { orchestrator, browserWorker, shop, profileRoot };
}

function checkoutProfileReady(task) {
  const prep = task?.config?.data?.checkoutPreparation;
  return Boolean(prep?.profileReady || prep?.profile?.requiredTargetsSatisfied);
}

async function runTask(runtime, config, runLabel, timeoutMs = 300000) {
  const task = runtime.orchestrator.createTask(config);
  append({ source: "parent", action: "probe.run.start", taskId: task.id, runLabel, result: "ok" });
  await startAndWait(runtime, task, runLabel, timeoutMs);
  return task;
}

async function startAndWait(runtime, task, runLabel, timeoutMs = 300000, starter) {
  const requirePayment = Boolean(task?.config?.data?.["__paymentSession"]);
  let settled = false;
  const done = new Promise(resolve => {
    const timer = setTimeout(() => resolve({ reason: "timeout" }), timeoutMs);
    const unsubscribe = runtime.orchestrator.on("taskUpdated", updated => {
      if (updated.id !== task.id) return;
      const prep = updated?.config?.data?.checkoutPreparation;
      const ready = requirePayment
        ? Boolean(prep?.paymentReady || prep?.reviewReady)
        : Boolean(prep?.profileReady || prep?.profile?.requiredTargetsSatisfied);
      if (updated.state === TaskState.CHECKOUT || ready || updated.state === TaskState.FAILED) {
        clearTimeout(timer);
        unsubscribe();
        resolve({
          reason: updated.state === TaskState.FAILED
            ? "failed"
            : updated.state === TaskState.CHECKOUT
              ? "checkout-state"
              : requirePayment
                ? "payment-ready"
                : "profile-ready",
          task: updated
        });
      }
    });
  });

  const started = Promise.resolve()
    .then(() => starter ? starter() : runtime.orchestrator.startTask(task.id))
    .catch(error => {
      append({ source: "parent", action: "probe.startTask.error", taskId: task.id, runLabel, result: "error", error: error.message });
    })
    .finally(() => { settled = true; });

  const outcome = await done;
  const current = runtime.orchestrator.getTask(task.id) || task;
  append({
    source: "parent",
    action: "probe.run.condition",
    taskId: task.id,
    runLabel,
    result: outcome.reason,
    state: current.state,
    checkoutPreparation: current.config?.data?.checkoutPreparation,
    earlyGateFlow: current.config?.data?.earlyGateFlow
  });

  if (![TaskState.SUCCESS, TaskState.FAILED, TaskState.CANCELLED].includes(current.state)) {
    runtime.orchestrator.cancelTask(task.id);
    append({ source: "parent", action: "probe.run.cancel", taskId: task.id, runLabel, result: "requested" });
  }

  for (let i = 0; i < 120 && !settled; i += 1) await wait(500);
  await started;
  append({
    source: "parent",
    action: "probe.run.end",
    taskId: task.id,
    runLabel,
    result: current.lastError ? "error" : "ok",
    state: current.state,
    error: current.lastError
  });
}

async function main() {
  const baseUrl = process.env.ARES_OFFLINE_BASE_URL || "http://127.0.0.1:18080";
  const requestedRuns = Number(process.env.ARES_PROBE_RUNS || 5);
  const runLimit = Number.isFinite(requestedRuns) ? Math.min(5, Math.max(1, Math.floor(requestedRuns))) : 5;
  append({ source: "parent", action: "probe.suite.start", result: "ok", baseUrl, timingLog });

  let runtime = makeRuntime(baseUrl, "main");
  await runtime.browserWorker.setCaptchaConfig({ mode: "siglip", keys: {} });
  await runtime.browserWorker.setFinalPurchaseAllowed(false);

  const summaries = [];
  summaries.push(await runTask(runtime, makeTaskConfig("cold-start", 1), "cold-start"));
  if (runLimit <= 1) {
    await runtime.browserWorker.close();
    runtime.orchestrator.cleanup();
    append({ source: "parent", action: "probe.suite.end", result: "ok", runs: summaries.length, timingLog, summaryPath });
    return;
  }
  summaries.push(await runTask(runtime, makeTaskConfig("warm-start", 2), "warm-start"));
  if (runLimit <= 2) {
    await runtime.browserWorker.close();
    runtime.orchestrator.cleanup();
    append({ source: "parent", action: "probe.suite.end", result: "ok", runs: summaries.length, timingLog, summaryPath });
    return;
  }
  summaries.push(await runTask(runtime, makeTaskConfig("new-task", 3), "new-task"));
  if (runLimit <= 3) {
    await runtime.browserWorker.close();
    runtime.orchestrator.cleanup();
    append({ source: "parent", action: "probe.suite.end", result: "ok", runs: summaries.length, timingLog, summaryPath });
    return;
  }

  const repeatedConfig = makeTaskConfig("repeated-task", 4);
  const repeated = await runTask(runtime, repeatedConfig, "repeated-task-initial");
  if ([TaskState.CANCELLED, TaskState.FAILED, TaskState.SUCCESS].includes(repeated.state)) {
    append({ source: "parent", action: "probe.repeat.restart", taskId: repeated.id, runLabel: "repeated-task", result: "requested" });
    await startAndWait(runtime, repeated, "repeated-task", 300000, () => runtime.orchestrator.restartTask(repeated.id));
    const current = runtime.orchestrator.getTask(repeated.id);
    summaries.push(current || repeated);
  }
  if (runLimit <= 4) {
    await runtime.browserWorker.close();
    runtime.orchestrator.cleanup();
    append({ source: "parent", action: "probe.suite.end", result: "ok", runs: summaries.length, timingLog, summaryPath });
    return;
  }

  await runtime.browserWorker.close();
  runtime.orchestrator.cleanup();
  append({ source: "parent", action: "probe.worker.restart", result: "closed-main-worker" });

  runtime = makeRuntime(baseUrl, "restart");
  await runtime.browserWorker.setCaptchaConfig({ mode: "siglip", keys: {} });
  await runtime.browserWorker.setFinalPurchaseAllowed(false);
  summaries.push(await runTask(runtime, makeTaskConfig("worker-browser-restart", 5), "worker-browser-restart"));
  await runtime.browserWorker.close();
  runtime.orchestrator.cleanup();

  const summary = {
    suiteId,
    timingLog,
    runs: summaries.map(task => ({
      taskId: task.id,
      state: task.state,
      lastError: task.lastError,
      earlyGateFlow: task.config?.data?.earlyGateFlow,
      checkoutPreparation: task.config?.data?.checkoutPreparation
    }))
  };
  fs.writeFileSync(summaryPath, JSON.stringify(summary, null, 2), "utf8");
  append({ source: "parent", action: "probe.suite.end", result: "ok", summaryPath });
  console.log(JSON.stringify({ timingLog, summaryPath }, null, 2));
}

main().catch(error => {
  append({ source: "parent", action: "probe.suite.error", result: "error", error: error instanceof Error ? error.stack || error.message : String(error) });
  console.error(error);
  process.exit(1);
});
