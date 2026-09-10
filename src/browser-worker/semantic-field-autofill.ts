import type { Locator, Page } from "./types";
import type { UiInteractionHelper } from "./ui-interaction-helper";
import {
  collectFieldDescriptors,
  fieldLocator,
  type FieldSemanticResolver
} from "./field-semantic-resolver";
import {
  fallbackTraceResolution,
  unknownTraceResolution,
  type SemanticCheckoutTraceRecorder,
  type SemanticCheckoutTraceResolution,
  type SemanticCheckoutTraceSnapshot
} from "./semantic-checkout-observability";
import {
  targetKey,
  type FieldIntent,
  type SemanticTarget,
  type SemanticTargetKey
} from "./semantic-target";
import type { SemanticFieldValueSource } from "./semantic-target-values";

export interface SemanticAutofillResult {
  filled: SemanticTarget[];
  missing: SemanticTarget[];
  writeCounts: Record<string, number>;
  trace?: SemanticCheckoutTraceSnapshot;
}

export interface SemanticAutofillWriteTraceOptions {
  kind?: "semantic" | "fallback";
  resolution?: SemanticCheckoutTraceResolution;
}

interface TracedSemanticFieldValueSource extends SemanticFieldValueSource {
  semanticCheckoutTrace?: SemanticCheckoutTraceRecorder;
}

function normalizeValue(value: string): string {
  return value.trim();
}

function semanticAutofillEnabled(values: SemanticFieldValueSource): boolean {
  const policy = values as SemanticFieldValueSource & { semanticAutofillEnabled?: boolean };
  return policy.semanticAutofillEnabled !== false;
}

function semanticCheckoutTrace(values: SemanticFieldValueSource): SemanticCheckoutTraceRecorder | undefined {
  return (values as TracedSemanticFieldValueSource).semanticCheckoutTrace;
}

export class SemanticFieldAutofill {
  private readonly completedTargets = new Map<SemanticTargetKey, Locator>();
  private readonly writeCounts = new Map<SemanticTargetKey, number>();
  private readonly seenTargets = new Map<SemanticTargetKey, SemanticTarget>();
  private trace?: SemanticCheckoutTraceRecorder;

  constructor(
    private readonly page: Page,
    private readonly interactions: UiInteractionHelper,
    private readonly resolver: FieldSemanticResolver,
    trace?: SemanticCheckoutTraceRecorder
  ) {
    this.trace = trace;
  }

  async fillSemantic(values: SemanticFieldValueSource): Promise<void> {
    // Bind the checkout-run recorder before the feature gate so deterministic
    // shop fallbacks remain observable even when KI AutoFill is disabled.
    this.trace ??= semanticCheckoutTrace(values);

    // A disabled KI AutoFill policy intentionally skips semantic DOM resolution.
    // Shop compatibility fallbacks may still fill deterministic known selectors.
    if (!semanticAutofillEnabled(values)) return;

    const descriptors = await collectFieldDescriptors(this.page);
    const resolved = await this.resolver.resolve(descriptors);
    const ranked = [...resolved].sort((left, right) => right.confidence - left.confidence);

    for (const item of ranked) {
      const resolution: SemanticCheckoutTraceResolution = {
        resolverSource: {
          intent: item.source.intent,
          context: item.source.context
        },
        confidence: item.confidence
      };

      if (item.target.intent === "unknown") {
        this.trace?.record({
          target: item.target,
          ...resolution,
          valueAvailable: false,
          action: "resolve",
          result: "unresolved"
        });
        continue;
      }

      const locator = fieldLocator(this.page, item.descriptor.index);
      const value = values.valueFor(item.target);
      if (item.descriptor.tagName === "select") {
        await this.selectLocator(item.target, locator, value, { kind: "semantic", resolution }).catch(() => false);
      } else {
        await this.fillLocator(item.target, locator, value, { kind: "semantic", resolution }).catch(() => false);
      }
    }
  }

  async fillLocator(
    target: SemanticTarget,
    locator: Locator,
    value: string | undefined,
    traceOptions: SemanticAutofillWriteTraceOptions = {}
  ): Promise<boolean> {
    const kind = this.traceKind(traceOptions);
    const resolution = this.traceResolution(traceOptions);
    const valueAvailable = Boolean(value?.trim());
    const action = kind === "fallback" ? "fallback-write" : "write";

    if (target.intent === "unknown") {
      this.trace?.record({
        target,
        ...resolution,
        valueAvailable,
        action: "resolve",
        result: "unresolved"
      });
      return false;
    }

    if (!await this.isInteractive(locator)) {
      this.trace?.record({
        target,
        ...resolution,
        valueAvailable,
        action: "interaction-check",
        result: "non-interactive"
      });
      return false;
    }

    const key = this.rememberTarget(target);
    const desired = normalizeValue(value ?? "");
    if (!desired) {
      this.trace?.record({
        target,
        ...resolution,
        valueAvailable: false,
        action: "value-check",
        result: "missing-value"
      });
      return false;
    }

    if (await this.isCompleted(target, desired)) {
      this.trace?.record({
        target,
        ...resolution,
        valueAvailable: true,
        action: "completion-check",
        result: "already-complete"
      });
      return true;
    }

    const current = await this.readValue(locator);
    if (normalizeValue(current) === desired) {
      this.completedTargets.set(key, locator);
      this.trace?.record({
        target,
        ...resolution,
        valueAvailable: true,
        action: "completion-check",
        result: "already-complete"
      });
      return true;
    }

    try {
      await this.interactions.fill(locator, desired, {
        attempts: 2,
        seed: `semantic-fill:${key}`
      });
    } catch (error) {
      this.trace?.record({
        target,
        ...resolution,
        valueAvailable: true,
        action,
        result: "write-failed"
      });
      throw error;
    }
    this.bumpWriteCount(target);

    const after = await this.readValue(locator);
    if (normalizeValue(after) !== desired) {
      this.trace?.record({
        target,
        ...resolution,
        valueAvailable: true,
        action,
        result: "write-failed"
      });
      return false;
    }

    this.completedTargets.set(key, locator);
    this.trace?.record({
      target,
      ...resolution,
      valueAvailable: true,
      action,
      result: kind === "fallback" ? "fallback-filled" : "filled"
    });
    return true;
  }

  async selectLocator(
    target: SemanticTarget,
    locator: Locator,
    value: string | undefined,
    traceOptions: SemanticAutofillWriteTraceOptions = {}
  ): Promise<boolean> {
    const kind = this.traceKind(traceOptions);
    const resolution = this.traceResolution(traceOptions);
    const valueAvailable = Boolean(value?.trim());
    const action = kind === "fallback" ? "fallback-select" : "select";

    if (target.intent === "unknown") {
      this.trace?.record({
        target,
        ...resolution,
        valueAvailable,
        action: "resolve",
        result: "unresolved"
      });
      return false;
    }

    if (!await this.isInteractive(locator)) {
      this.trace?.record({
        target,
        ...resolution,
        valueAvailable,
        action: "interaction-check",
        result: "non-interactive"
      });
      return false;
    }

    const key = this.rememberTarget(target);
    const desired = normalizeValue(value ?? "");
    if (!desired) {
      this.trace?.record({
        target,
        ...resolution,
        valueAvailable: false,
        action: "value-check",
        result: "missing-value"
      });
      return false;
    }

    if (await this.isCompleted(target, desired)) {
      this.trace?.record({
        target,
        ...resolution,
        valueAvailable: true,
        action: "completion-check",
        result: "already-complete"
      });
      return true;
    }

    const current = await this.readValue(locator);
    if (normalizeValue(current).toUpperCase() === desired.toUpperCase()) {
      this.completedTargets.set(key, locator);
      this.trace?.record({
        target,
        ...resolution,
        valueAvailable: true,
        action: "completion-check",
        result: "already-complete"
      });
      return true;
    }

    try {
      await this.interactions.select(locator, desired, {
        attempts: 2,
        seed: `semantic-select:${key}`
      });
    } catch (error) {
      this.trace?.record({
        target,
        ...resolution,
        valueAvailable: true,
        action,
        result: "write-failed"
      });
      throw error;
    }
    this.bumpWriteCount(target);

    const after = await this.readValue(locator);
    if (normalizeValue(after).toUpperCase() !== desired.toUpperCase()) {
      this.trace?.record({
        target,
        ...resolution,
        valueAvailable: true,
        action,
        result: "write-failed"
      });
      return false;
    }

    this.completedTargets.set(key, locator);
    this.trace?.record({
      target,
      ...resolution,
      valueAvailable: true,
      action,
      result: kind === "fallback" ? "fallback-filled" : "filled"
    });
    return true;
  }

  async isComplete(target: SemanticTarget, value: string): Promise<boolean> {
    this.rememberTarget(target);
    return this.isCompleted(target, value);
  }

  observedTargets(): SemanticTarget[] {
    return [...this.seenTargets.values()].map(target => ({ ...target }));
  }

  hasObservedIntent(intent: Exclude<FieldIntent, "unknown">): boolean {
    return [...this.seenTargets.values()].some(target => target.intent === intent);
  }

  async result(
    values: SemanticFieldValueSource,
    targets: SemanticTarget[] = [...this.seenTargets.values()]
  ): Promise<SemanticAutofillResult> {
    this.trace ??= semanticCheckoutTrace(values);

    const filled: SemanticTarget[] = [];
    const missing: SemanticTarget[] = [];
    const uniqueTargets = new Map<SemanticTargetKey, SemanticTarget>();

    for (const target of targets) uniqueTargets.set(targetKey(target), target);

    for (const target of uniqueTargets.values()) {
      const value = values.valueFor(target);
      if (!value?.trim()) {
        missing.push({ ...target });
        continue;
      }
      if (await this.isCompleted(target, value)) filled.push({ ...target });
      else missing.push({ ...target });
    }

    const writeCounts: Record<string, number> = {};
    for (const [key, count] of this.writeCounts.entries()) writeCounts[key] = count;
    return {
      filled,
      missing,
      writeCounts,
      ...(this.trace ? { trace: this.trace.snapshot() } : {})
    };
  }

  private async isCompleted(target: SemanticTarget, value: string): Promise<boolean> {
    const key = targetKey(target);
    const locator = this.completedTargets.get(key);
    if (!locator) return false;
    const current = await this.readValue(locator);
    if (normalizeValue(current).toUpperCase() === normalizeValue(value).toUpperCase()) return true;
    this.completedTargets.delete(key);
    return false;
  }

  private rememberTarget(target: SemanticTarget): SemanticTargetKey {
    const key = targetKey(target);
    this.seenTargets.set(key, { ...target });
    return key;
  }

  private async isInteractive(locator: Locator): Promise<boolean> {
    if (!await locator.isVisible({ timeout: 150 }).catch(() => false)) return false;
    return locator.isEnabled({ timeout: 150 }).catch(() => false);
  }

  private async readValue(locator: Locator): Promise<string> {
    return locator.inputValue({ timeout: 250 }).catch(() => "");
  }

  private bumpWriteCount(target: SemanticTarget): void {
    const key = targetKey(target);
    this.writeCounts.set(key, (this.writeCounts.get(key) ?? 0) + 1);
  }

  private traceKind(options: SemanticAutofillWriteTraceOptions): "semantic" | "fallback" {
    if (options.kind) return options.kind;
    return options.resolution ? "semantic" : "fallback";
  }

  private traceResolution(options: SemanticAutofillWriteTraceOptions): SemanticCheckoutTraceResolution {
    if (options.resolution) return options.resolution;
    return this.traceKind(options) === "fallback" ? fallbackTraceResolution() : unknownTraceResolution();
  }
}
