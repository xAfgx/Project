import { FieldSemanticResolver, type FieldDescriptor, type ResolvedField } from "./field-semantic-resolver";
import { targetKey, type SemanticTarget } from "./semantic-target";

export interface ObservedSemanticField extends FieldDescriptor {
  fieldId: string;
}

export interface SemanticExecutionPlanItem {
  fieldId: string;
  intent: SemanticTarget["intent"];
  context: SemanticTarget["context"];
  confidence: number;
  value: string;
}

export interface SemanticExecutionResult {
  planned: number;
  applied: number;
  verified: boolean;
  results: Array<{
    fieldId: string;
    intent: string;
    context: string;
    confidence: number;
    verified: boolean;
    reason?: string;
    observedValue?: string;
  }>;
  fallbackNeeded: Array<{
    fieldId: string;
    intent: string;
    context: string;
    confidence: number;
    verified: boolean;
    reason?: string;
    observedValue?: string;
  }>;
}

export type SemanticValueBag = Readonly<Record<string, string | undefined>>;

export interface SemanticInteractionExecutor {
  observeFields(): Promise<ObservedSemanticField[]>;
  executePlan(plan: SemanticExecutionPlanItem[]): Promise<SemanticExecutionResult>;
}

export interface UnifiedInteractionRun {
  observed: number;
  resolved: number;
  planned: number;
  unresolved: Array<{ fieldId: string; reason: string }>;
  execution: SemanticExecutionResult;
}

export class UnifiedInteractionPipeline {
  constructor(
    private readonly executor: SemanticInteractionExecutor,
    private readonly resolver: FieldSemanticResolver = new FieldSemanticResolver(),
    private readonly minimumConfidence = 0.78
  ) {}

  async autofill(values: SemanticValueBag): Promise<UnifiedInteractionRun> {
    const observed = await this.executor.observeFields();
    const resolved = await this.resolver.resolve(observed);
    const plan: SemanticExecutionPlanItem[] = [];
    const unresolved: Array<{ fieldId: string; reason: string }> = [];

    resolved.forEach((field, index) => {
      const observedField = observed[index];
      if (!observedField) return;
      const decision = this.toPlanItem(observedField, field, values);
      if (decision.item) plan.push(decision.item);
      else unresolved.push({ fieldId: observedField.fieldId, reason: decision.reason });
    });

    const execution = plan.length
      ? await this.executor.executePlan(plan)
      : { planned: 0, applied: 0, verified: true, results: [], fallbackNeeded: [] };

    return {
      observed: observed.length,
      resolved: resolved.filter(field => field.target.intent !== "unknown").length,
      planned: plan.length,
      unresolved,
      execution
    };
  }

  private toPlanItem(
    observed: ObservedSemanticField,
    resolved: ResolvedField,
    values: SemanticValueBag
  ): { item?: SemanticExecutionPlanItem; reason: string } {
    if (resolved.target.intent === "unknown") return { reason: "unknown-intent" };
    if (resolved.confidence < this.minimumConfidence) return { reason: "low-confidence" };

    const exactKey = targetKey(resolved.target);
    const genericKey = targetKey({ intent: resolved.target.intent, context: "unknown" });
    const value = values[exactKey] ?? values[genericKey];
    if (typeof value !== "string") return { reason: "missing-value" };

    return {
      reason: "",
      item: {
        fieldId: observed.fieldId,
        intent: resolved.target.intent,
        context: resolved.target.context,
        confidence: resolved.confidence,
        value
      }
    };
  }
}
