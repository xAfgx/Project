import type { Locator, Page } from "./types";
import type { UiInteractionHelper } from "./ui-interaction-helper";
import {
  collectFieldDescriptors,
  fieldLocator,
  type FieldHost,
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

    // Checkout forms are frequently rendered inside a nested (OOPIF/srcdoc)
    // frame, so every frame is resolved and written independently. Indices are
    // frame-local: collectFieldDescriptors and fieldLocator must use the same
    // host for a given batch.
    for (const host of this.fieldHosts()) {
      const descriptors = await collectFieldDescriptors(host).catch(() => []);
      if (!descriptors.length) continue;
      const resolved = await this.resolver.resolve(descriptors);
      // DOM order, not confidence order: the form is filled top-to-bottom the
      // way a person would move through it.
      for (const item of resolved) {
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

        const locator = fieldLocator(host, item.descriptor.index);
        const value = values.valueFor(item.target);
        if (item.descriptor.tagName === "select") {
          await this.selectLocator(item.target, locator, value, { kind: "semantic", resolution }).catch(() => false);
        } else {
          await this.fillLocator(item.target, locator, value, { kind: "semantic", resolution }).catch(() => false);
        }
      }
    }
  }

  private fieldHosts(): FieldHost[] {
    const frames = typeof this.page.frames === "function" ? this.page.frames() : [];
    return frames.length ? frames : [this.page];
  }

  /** Visible form controls across the page and every frame (load gate). */
  async observeCount(): Promise<number> {
    let total = 0;
    for (const host of this.fieldHosts()) {
      const descriptors = await collectFieldDescriptors(host).catch(() => []);
      total += descriptors.length;
    }
    return total;
  }

  /**
   * True when at least one real checkout control (not the site search box) is
   * enabled. The Global-E checkout shows a blocking loading spinner first and
   * keeps its fields disabled until it is gone, so autofill must wait for this.
   */
  async observeReady(): Promise<boolean> {
    for (const host of this.fieldHosts()) {
      const descriptors = await collectFieldDescriptors(host).catch(() => []);
      for (const descriptor of descriptors) {
        if (descriptor.inputType === "search") continue;
        if (!descriptor.name && !descriptor.id && !descriptor.autocomplete) continue;
        const locator = fieldLocator(host, descriptor.index);
        if (await locator.isEnabled({ timeout: 120 }).catch(() => false)) return true;
      }
    }
    return false;
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
      // Type character-by-character through the native input pipeline instead of
      // pasting the value. This keeps per-keystroke formatters/masks working and
      // avoids synthetic isTrusted=false input events.
      await this.interactions.type(locator, desired, {
        attempts: 2,
        seed: `semantic-type:${key}`,
        interKeyDelayMinMs: 45,
        interKeyDelayMaxMs: 120,
        // Occasional self-corrected typo so the entry looks human.
        typoProbability: 0.22
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

    // Selects frequently store numeric ids (country DE -> value 69) or a
    // localized label. Resolve the option once by value, visible text or
    // localized region name, so a correct preselection is recognised instead of
    // being overwritten by a doomed write, and a wrong one is written through
    // the option's own value.
    const option = await this.resolveSelectOption(locator, desired);
    if (option.alreadySelected) {
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
    const desiredValue = option.matched && option.value ? option.value : desired;

    try {
      // Selects fail fast: when the option value/format does not exist there is
      // no point retrying for seconds.
      await this.interactions.select(locator, desiredValue, {
        attempts: 1,
        verifyTimeoutMs: 500,
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
    const normalizedAfter = normalizeValue(after).toUpperCase();
    if (normalizedAfter !== desiredValue.toUpperCase() && normalizedAfter !== desired.toUpperCase()) {
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

  private async resolveSelectOption(
    locator: Locator,
    desired: string
  ): Promise<{ matched: boolean; alreadySelected: boolean; value: string }> {
    const resolved = await locator.evaluate((element, args) => {
      const select = element as HTMLSelectElement;
      const code = String(args.code || "").trim().toUpperCase();
      let localized = "";
      try {
        const ctor = (Intl as unknown as {
          DisplayNames?: new (locales: string[], options: { type: string }) => { of(value: string): string | undefined };
        }).DisplayNames;
        const locale = document.documentElement.lang || "en";
        if (ctor) localized = String(new ctor([locale, "en"], { type: "region" }).of(code) || "");
      } catch {
        localized = "";
      }
      const wanted = new Set<string>();
      if (code) wanted.add(code);
      if (localized.trim()) wanted.add(localized.trim().toUpperCase());
      const items = Array.from(select.options || []).map(option => ({
        value: String(option.value ?? ""),
        text: String(option.textContent ?? "").trim(),
        selected: option.selected === true
      }));
      const matches = (item: { value: string; text: string }): boolean =>
        wanted.has(item.value.trim().toUpperCase()) || wanted.has(item.text.toUpperCase());
      const selected = items.find(item => item.selected && matches(item));
      if (selected) return { matched: true, alreadySelected: true, value: selected.value };
      const candidate = items.find(item => matches(item));
      if (candidate) return { matched: true, alreadySelected: false, value: candidate.value };
      return { matched: false, alreadySelected: false, value: "" };
    }, { code: desired }).catch(() => ({ matched: false, alreadySelected: false, value: "" }));
    const record = resolved && typeof resolved === "object" ? resolved as Record<string, unknown> : {};
    return {
      matched: record["matched"] === true,
      alreadySelected: record["alreadySelected"] === true,
      value: String(record["value"] ?? "")
    };
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
