import { targetKey, type SemanticTarget, type SemanticTargetKey } from "./semantic-target";

export interface SemanticTargetValue {
  target: SemanticTarget;
  value: string;
}

export interface SemanticFieldValueSource {
  valueFor(target: SemanticTarget): string | undefined;
}

export class SemanticTargetValueMap implements SemanticFieldValueSource {
  private readonly values = new Map<SemanticTargetKey, SemanticTargetValue>();

  constructor(entries: SemanticTargetValue[] = []) {
    for (const entry of entries) this.set(entry.target, entry.value);
  }

  set(target: SemanticTarget, value: string | undefined): void {
    const normalized = value?.trim();
    const key = targetKey(target);
    if (!normalized) {
      this.values.delete(key);
      return;
    }
    this.values.set(key, { target: { ...target }, value: normalized });
  }

  valueFor(target: SemanticTarget): string | undefined {
    return this.values.get(targetKey(target))?.value;
  }

  entries(): SemanticTargetValue[] {
    return [...this.values.values()].map(entry => ({ target: { ...entry.target }, value: entry.value }));
  }
}
