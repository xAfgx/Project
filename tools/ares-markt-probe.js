const fs = require("fs");
const os = require("os");
const path = require("path");

const repoRoot = path.resolve(__dirname, "..");
const logDir = path.join(repoRoot, "runtime-probe-logs");
fs.mkdirSync(logDir, { recursive: true });

const suiteId = new Date().toISOString().replace(/[:.]/g, "-");
const summaryPath = path.join(logDir, `markt-${suiteId}.summary.json`);
const marktBaseUrl = process.env.ARES_MARKT_BASE_URL || "http://127.0.0.1:18090";
const pokemonBaseUrl = process.env.ARES_OFFLINE_BASE_URL || "http://127.0.0.1:18080";
const concurrent = process.env.ARES_PROBE_CONCURRENT === "1";
const headless = process.env.ARES_PROBE_HEADLESS !== "0";
const timeoutMs = Number(process.env.ARES_PROBE_TIMEOUT_MS || 420000);

process.env.ARES_CAPTCHA_MODE = process.env.ARES_CAPTCHA_MODE || "siglip";

const { TaskOrchestrator } = require("../dist/backend/orchestrator");
const { TaskRepositoryMock, WorkerMock } = require("../dist/backend/mocks");
const { BrowserWorkerPoolClient } = require("../dist/backend/browser-worker/client");
const { TaskState } = require("../dist/backend/models");

function log(event) {
  process.stderr.write(`${new Date().toLocaleTimeString()} ${JSON.stringify(event)}\n`);
}

function makeProfile() {
  return {
    id: "default-profile",
    name: "MediaMarkt Runtime Probe Profile",
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
    browser: { headless, kiAutofill: true }
  };
}

function cardSession() {
  return {
    method: "card",
    card: {
      holderName: "Max Mustermann",
      cardNumber: "4545454554",
      expiry: "12/30",
      securityCode: "123"
    }
  };
}

function makeShops() {
  return new Map([
    ["mediamarkt-offline", {
      id: "mediamarkt-offline",
      name: "MediaMarkt Offline Runtime Flow",
      baseUrl: marktBaseUrl,
      platform: "mediamarkt",
      config: {}
    }],
    ["pokemon-center-offline", {
      id: "pokemon-center-offline",
      name: "Pokemon Center Offline Runtime Flow",
      baseUrl: pokemonBaseUrl,
      platform: "pokemon-center",
      config: {}
    }]
  ]);
}

function marktTaskConfig() {
  return {
    id: `direct_markt-probe-live`,
    name: "MediaMarkt Runtime Probe",
    shopId: "mediamarkt-offline",
    maxRetries: 0,
    data: {
      profileId: "default-profile",
      // Real direct-lane shape created by the UI (no monitor parent).
      searchTerm: "AirTag",
      browserConfig: { headless },
      proxySelection: { mode: "profile-default" },
      __paymentSession: cardSession(),
      discoveryMaxMs: 120000,
      checkoutFieldLoadMaxMs: 60000,
      checkoutPreparationMaxMs: 120000,
      orderConfirmationAttempts: 2,
      orderConfirmationRetryDelayMs: 750
    }
  };
}

function pokemonTaskConfig() {
  return {
    id: `pokemon-probe-${suiteId}`,
    name: "Pokemon Center Concurrent Probe",
    shopId: "pokemon-center-offline",
    maxRetries: 0,
    data: {
      profileId: "default-profile",
      monitorStrategy: {
        mode: "early-gate",
        productName: "Wald Puzzle",
        discoveryKeywords: ["Wald Puzzle", "Waldszene Puzzle", "Puzzle"]
      },
      postQueueDiscovery: {
        productName: "Wald Puzzle",
        keywords: ["Wald Puzzle", "Waldszene Puzzle", "Puzzle"]
      },
      __paymentSession: cardSession(),
      postQueueEntryUrl: "/de-de",
      browserConfig: { headless, queueMaxWaitMs: 120000 },
      discoveryMaxMs: 120000,
      checkoutFieldLoadMaxMs: 120000,
      checkoutPreparationMaxMs: 120000,
      orderConfirmationAttempts: 2,
      orderConfirmationRetryDelayMs: 750
    }
  };
}

function waitForOutcome(orchestrator, task, label) {
  return new Promise(resolve => {
    const timer = setTimeout(() => {
      cleanup();
      resolve({ label, reason: "timeout" });
    }, timeoutMs);
    const off = orchestrator.on("taskUpdated", updated => {
      if (updated.id !== task.id) return;
      const prep = updated.config?.data?.checkoutPreparation;
      if (updated.state === TaskState.FAILED) {
        cleanup();
        resolve({ label, reason: "failed", error: updated.lastError });
        return;
      }
      if (prep?.paymentReady === true || prep?.reviewReady === true) {
        cleanup();
        resolve({ label, reason: "payment-ready", state: updated.state });
        return;
      }
      if (updated.state === TaskState.SUCCESS) {
        cleanup();
        resolve({ label, reason: "success" });
      }
    });
    function cleanup() {
      clearTimeout(timer);
      off();
    }
  });
}

async function startAndWait(orchestrator, task, label) {
  const outcome = waitForOutcome(orchestrator, task, label);
  // startTask resolves only when the executor returns; the executor waits in
  // the final-purchase guard on purpose, so it must run in the background.
  const started = Promise.resolve()
    .then(() => orchestrator.startTask(task.id))
    .catch(error => log({ action: "probe.start.error", label, error: error.message }));
  const result = await outcome;
  const current = orchestrator.getTask(task.id) || task;
  if (![TaskState.SUCCESS, TaskState.FAILED, TaskState.CANCELLED].includes(current.state)) {
    try { await orchestrator.cancelTask(task.id); } catch { /* cancellation is best effort */ }
    log({ action: "probe.cancel", label, taskId: task.id });
  }
  await Promise.race([started, new Promise(resolve => setTimeout(resolve, 20000))]);
  const summary = {
    label,
    reason: result.reason,
    state: current.state,
    error: current.lastError,
    releaseProduct: current.config?.data?.releaseProduct,
    mediamarktFlow: current.config?.data?.mediamarktFlow,
    checkoutPreparation: current.config?.data?.checkoutPreparation,
    finalPurchaseRuntime: current.config?.data?.finalPurchaseRuntime
  };
  log({ action: "probe.result", ...summary });
  return summary;
}

async function main() {
  const shops = makeShops();
  const profile = makeProfile();
  const profileRoot = path.join(os.tmpdir(), "ares-markt-probe-live");
  const browserWorker = new BrowserWorkerPoolClient(
    shopId => shops.get(shopId),
    profileId => profileId === profile.id ? profile : undefined,
    {
      processCount: 1,
      profileRoot,
      requestTimeoutMs: 120000,
      executeTimeoutMs: 600000,
      heartbeatIntervalMs: 10000,
      heartbeatTimeoutMs: 10000,
      onTaskUpdate: task => {
        const prep = task.config?.data?.checkoutPreparation;
        if (prep?.phase) log({ action: "probe.prep", taskId: task.id, phase: prep.phase, profileReady: prep.profileReady, paymentReady: prep.paymentReady, reason: prep.paymentReadinessReason });
      }
    }
  );
  const orchestrator = new TaskOrchestrator(new TaskRepositoryMock(), browserWorker);
  orchestrator.addWorker(new WorkerMock("probe-slot-markt"));
  await browserWorker.setFinalPurchaseAllowed(false);

  const runs = [];
  const tasks = [orchestrator.createTask(marktTaskConfig())];
  if (concurrent) tasks.push(orchestrator.createTask(pokemonTaskConfig()));
  log({ action: "probe.start", concurrent, marktBaseUrl, pokemonBaseUrl, tasks: tasks.map(task => task.id) });

  runs.push(...await Promise.all(tasks.map((task, index) => startAndWait(orchestrator, task, index === 0 ? "mediamarkt" : "pokemon-center"))));

  await browserWorker.close().catch(() => undefined);
  orchestrator.cleanup();
  const summary = { suiteId, concurrent, runs };
  fs.writeFileSync(summaryPath, JSON.stringify(summary, null, 2), "utf8");
  log({ action: "probe.suite.end", summaryPath, runs: runs.map(run => ({ label: run.label, reason: run.reason, state: run.state })) });
  console.log(JSON.stringify(summary, null, 2));
  process.exit(runs.every(run => run.reason === "payment-ready" || run.reason === "success") ? 0 : 1);
}

main().catch(error => {
  log({ action: "probe.suite.error", error: error instanceof Error ? error.stack || error.message : String(error) });
  process.exit(1);
});
