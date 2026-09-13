const fs = require("fs");
const Module = require("module");
const { performance } = require("perf_hooks");

const logPath = process.env.ARES_TIMING_LOG;
const workerStart = performance.now();
let sequence = 0;

function nowIso() {
  return new Date().toISOString();
}

function taskIdFrom(args) {
  for (const arg of args) {
    if (arg && typeof arg === "object") {
      if (typeof arg.id === "string" && arg.config) return arg.id;
      if (arg.task && typeof arg.task.id === "string") return arg.task.id;
    }
  }
  return process.env.ARES_TIMING_TASK_ID || undefined;
}

function safe(value) {
  if (value === undefined || value === null) return value;
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return value;
  if (Array.isArray(value)) return value.slice(0, 20).map(safe);
  if (typeof value === "object") {
    const out = {};
    for (const [key, item] of Object.entries(value).slice(0, 40)) {
      if (/value|email|phone|address|name/i.test(key)) continue;
      out[key] = safe(item);
    }
    return out;
  }
  return String(value);
}

function emit(event) {
  if (!logPath) return;
  const payload = {
    sequence: ++sequence,
    timestamp: nowIso(),
    elapsedMs: Math.round((performance.now() - workerStart) * 1000) / 1000,
    pid: process.pid,
    ...event
  };
  try {
    fs.appendFileSync(logPath, `${JSON.stringify(payload)}\n`, "utf8");
  } catch {}
}

function wrapAsync(proto, method, action, metaFn) {
  if (!proto || typeof proto[method] !== "function" || proto[method].__aresTimingWrapped) return;
  const original = proto[method];
  async function wrapped(...args) {
    const started = performance.now();
    const meta = metaFn ? safe(metaFn.call(this, args)) : {};
    emit({ phase: "start", action, taskId: taskIdFrom(args), ...meta });
    try {
      const result = await original.apply(this, args);
      emit({
        phase: "end",
        action,
        taskId: taskIdFrom(args),
        durationMs: Math.round((performance.now() - started) * 1000) / 1000,
        result: result && typeof result === "object" ? safe({
          success: result.success,
          attempts: result.attempts,
          failureReason: result.failureReason,
          targetState: result.targetState
        }) : safe(result),
        ...meta
      });
      return result;
    } catch (error) {
      emit({
        phase: "error",
        action,
        taskId: taskIdFrom(args),
        durationMs: Math.round((performance.now() - started) * 1000) / 1000,
        result: "error",
        error: error instanceof Error ? error.message : String(error),
        ...meta
      });
      throw error;
    }
  }
  wrapped.__aresTimingWrapped = true;
  proto[method] = wrapped;
}

function patchExports(filename, exportsValue) {
  if (!exportsValue || typeof exportsValue !== "object") return exportsValue;

  if (filename.endsWith("interaction-engine.js")) {
    const proto = exportsValue.InteractionEngine && exportsValue.InteractionEngine.prototype;
    wrapAsync(proto, "runFormAction", "interaction.formAction", args => ({
      waitReason: "scrollIntoViewIfNeeded + readiness + action + outcome",
      retryCount: args[1]?.attempts ?? 2,
      expectation: args[3]?.name
    }));
    wrapAsync(proto, "click", "interaction.click", args => ({
      waitReason: "scrollIntoViewIfNeeded + readiness + pointer + postClick",
      retryCount: args[1]?.attempts ?? 2
    }));
    wrapAsync(proto, "scrollIntoView", "interaction.scrollIntoView", args => ({
      waitReason: "locator.scrollIntoViewIfNeeded",
      retryCount: args[1]?.attempts ?? 2
    }));
    wrapAsync(proto, "movePointer", "interaction.movePointer", () => ({
      waitReason: "seeded pointer movement"
    }));
  }

  if (filename.endsWith("semantic-field-autofill.js")) {
    const proto = exportsValue.SemanticFieldAutofill && exportsValue.SemanticFieldAutofill.prototype;
    wrapAsync(proto, "fillSemantic", "semantic.fillSemantic", () => ({ waitReason: "field hosts + scan + classification + writes" }));
    wrapAsync(proto, "observeCount", "semantic.observeCount", () => ({ waitReason: "field scan load gate" }));
    wrapAsync(proto, "observeReady", "semantic.observeReady", () => ({ waitReason: "field enabled load gate" }));
    wrapAsync(proto, "fillLocator", "semantic.field.input", args => ({
      field: args[0] ? `${args[0].context}:${args[0].intent}` : undefined,
      waitReason: "interactive check + completion check + type + verify"
    }));
    wrapAsync(proto, "selectLocator", "semantic.field.select", args => ({
      field: args[0] ? `${args[0].context}:${args[0].intent}` : undefined,
      waitReason: "interactive check + completion check + select + verify"
    }));
  }

  if (filename.endsWith("field-semantic-resolver.js")) {
    const proto = exportsValue.FieldSemanticResolver && exportsValue.FieldSemanticResolver.prototype;
    wrapAsync(proto, "resolve", "semantic.classification", args => ({
      fieldCount: Array.isArray(args[0]) ? args[0].length : undefined,
      waitReason: "semantic field classification"
    }));
    if (typeof exportsValue.collectFieldDescriptors === "function" && !exportsValue.collectFieldDescriptors.__aresTimingWrapped) {
      const original = exportsValue.collectFieldDescriptors;
      exportsValue.collectFieldDescriptors = async function wrappedCollect(...args) {
        const started = performance.now();
        emit({ phase: "start", action: "semantic.fieldScan", waitReason: "DOM field descriptor scan" });
        try {
          const result = await original.apply(this, args);
          emit({
            phase: "end",
            action: "semantic.fieldScan",
            durationMs: Math.round((performance.now() - started) * 1000) / 1000,
            fieldCount: Array.isArray(result) ? result.length : undefined,
            result: "ok"
          });
          return result;
        } catch (error) {
          emit({
            phase: "error",
            action: "semantic.fieldScan",
            durationMs: Math.round((performance.now() - started) * 1000) / 1000,
            result: "error",
            error: error instanceof Error ? error.message : String(error)
          });
          throw error;
        }
      };
      exportsValue.collectFieldDescriptors.__aresTimingWrapped = true;
    }
  }

  if (filename.endsWith("semantic-checkout-preparer.js")) {
    const proto = exportsValue.SemanticCheckoutPreparer && exportsValue.SemanticCheckoutPreparer.prototype;
    wrapAsync(proto, "observeCount", "checkout.observeCount", () => ({ waitReason: "checkout field count gate" }));
    wrapAsync(proto, "observeReady", "checkout.observeReady", () => ({ waitReason: "checkout field readiness gate" }));
    wrapAsync(proto, "prepare", "checkout.prepare", () => ({ waitReason: "semantic profile planner + autofill loop" }));
  }

  if (filename.endsWith("early-gate-task-executor.js")) {
    const proto = exportsValue.EarlyGateBrowserTaskExecutor && exportsValue.EarlyGateBrowserTaskExecutor.prototype;
    wrapAsync(proto, "execute", "task.execute.earlyGate", args => ({ taskId: args[0]?.id, waitReason: "early gate child runtime" }));
    wrapAsync(proto, "waitForCheckoutFields", "checkout.waitForFields", args => ({ taskId: args[0]?.id, waitReason: "field count + ready polling" }));
    wrapAsync(proto, "prepareCheckoutUntilReady", "checkout.prepareUntilReady", args => ({ taskId: args[0]?.id, waitReason: "profile/payment/final readiness loop" }));
    wrapAsync(proto, "delay", "task.delay", args => ({ waitReason: `delay(${args[0]}ms)`, delayMs: args[0] }));
  }

  if (filename.endsWith("release-journey.js") && filename.includes(`${require("path").sep}pokemon-center${require("path").sep}`)) {
    const proto = exportsValue.PokemonCenterReleaseJourney && exportsValue.PokemonCenterReleaseJourney.prototype;
    wrapAsync(proto, "discover", "pokemon.discover", () => ({ waitReason: "new releases discovery + product click" }));
    wrapAsync(proto, "addToCart", "pokemon.addToCart", () => ({ waitReason: "add to cart + cart navigation" }));
    wrapAsync(proto, "openCheckout", "pokemon.openCheckout", () => ({ waitReason: "guest checkout + intl checkout navigation" }));
    wrapAsync(proto, "advanceCheckout", "pokemon.advanceCheckout", () => ({ waitReason: "safe checkout continue button search/click" }));
  }

  if (filename.endsWith("ares-browser-runtime.js")) {
    const proto = exportsValue.AresBrowserRuntime && exportsValue.AresBrowserRuntime.prototype;
    wrapAsync(proto, "createContext", "browser.createContext", args => ({ taskId: args[0]?.taskId, waitReason: "browser/profile context creation" }));
    wrapAsync(proto, "closeContext", "browser.closeContext", args => ({ taskId: args[0], waitReason: "browser/profile context cleanup" }));
    wrapAsync(proto, "health", "browser.health", () => ({ waitReason: "worker health" }));
  }

  return exportsValue;
}

const originalLoad = Module._load;
Module._load = function patchedLoad(request, parent, isMain) {
  const loaded = originalLoad.apply(this, arguments);
  try {
    const filename = Module._resolveFilename(request, parent, isMain);
    patchExports(String(filename), loaded);
  } catch {}
  return loaded;
};

emit({ phase: "start", action: "timing.hook.loaded", result: "ok" });
