import { targetKey, type SemanticTarget } from "./semantic-target";

export type SemanticCheckoutBillingMode =
  | "explicit-billing"
  | "same-as-shipping"
  | "separate-billing-fields";

export type SemanticCheckoutTraceResult =
  | "filled"
  | "missing-value"
  | "unresolved"
  | "non-interactive"
  | "already-complete"
  | "write-failed"
  | "fallback-filled";

export type SemanticCheckoutTraceAction =
  | "resolve"
  | "value-check"
  | "interaction-check"
  | "completion-check"
  | "write"
  | "select"
  | "fallback-write"
  | "fallback-select";

export type SemanticCheckoutResolverSource =
  | "standard-metadata"
  | "lexical"
  | "embedding"
  | "unknown"
  | "fallback";

export interface SemanticCheckoutResolverTraceSource {
  intent: SemanticCheckoutResolverSource;
  context: SemanticCheckoutResolverSource;
}

export interface SemanticCheckoutTraceEvent {
  targetKey: string;
  context: SemanticTarget["context"];
  intent: SemanticTarget["intent"];
  resolverSource: SemanticCheckoutResolverTraceSource;
  confidence: number | null;
  billingMode: SemanticCheckoutBillingMode;
  valueAvailable: boolean;
  action: SemanticCheckoutTraceAction;
  result: SemanticCheckoutTraceResult;
  timestamp: string;
}

export interface SemanticCheckoutTraceSnapshot {
  schemaVersion: 1;
  events: SemanticCheckoutTraceEvent[];
  droppedEvents: number;
}

export interface SemanticCheckoutTraceResolution {
  resolverSource: SemanticCheckoutResolverTraceSource;
  confidence: number | null;
}

export interface SemanticCheckoutTraceRecordInput extends SemanticCheckoutTraceResolution {
  target: SemanticTarget;
  valueAvailable: boolean;
  action: SemanticCheckoutTraceAction;
  result: SemanticCheckoutTraceResult;
}

const UNKNOWN_RESOLUTION: SemanticCheckoutTraceResolution = {
  resolverSource: { intent: "unknown", context: "unknown" },
  confidence: null
};

const FALLBACK_RESOLUTION: SemanticCheckoutTraceResolution = {
  resolverSource: { intent: "fallback", context: "fallback" },
  confidence: null
};

/**
 * PII-safe by construction: this recorder accepts semantic metadata only.
 * It intentionally has no fields for profile values, field labels, selectors,
 * free-form DOM text or error messages.
 */
export class SemanticCheckoutTraceRecorder {
  private readonly events: SemanticCheckoutTraceEvent[] = [];
  private droppedEvents = 0;

  constructor(
    private readonly billingMode: SemanticCheckoutBillingMode,
    private readonly maxEvents = 500
  ) {}

  record(input: SemanticCheckoutTraceRecordInput): void {
    if (this.events.length >= this.maxEvents) {
      this.droppedEvents += 1;
      return;
    }

    this.events.push({
      targetKey: targetKey(input.target),
      context: input.target.context,
      intent: input.target.intent,
      resolverSource: {
        intent: input.resolverSource.intent,
        context: input.resolverSource.context
      },
      confidence: input.confidence,
      billingMode: this.billingMode,
      valueAvailable: input.valueAvailable,
      action: input.action,
      result: input.result,
      timestamp: new Date().toISOString()
    });
  }

  snapshot(): SemanticCheckoutTraceSnapshot {
    return {
      schemaVersion: 1,
      events: this.events.map(event => ({
        ...event,
        resolverSource: { ...event.resolverSource }
      })),
      droppedEvents: this.droppedEvents
    };
  }
}

export function unknownTraceResolution(): SemanticCheckoutTraceResolution {
  return {
    resolverSource: { ...UNKNOWN_RESOLUTION.resolverSource },
    confidence: UNKNOWN_RESOLUTION.confidence
  };
}

export function fallbackTraceResolution(): SemanticCheckoutTraceResolution {
  return {
    resolverSource: { ...FALLBACK_RESOLUTION.resolverSource },
    confidence: FALLBACK_RESOLUTION.confidence
  };
}
