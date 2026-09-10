import { targetKey, type SemanticTarget, type SemanticTargetKey } from "./semantic-target";

export interface SemanticCompletionState {
  filled: readonly SemanticTarget[];
  missing: readonly SemanticTarget[];
  requiredTargets: readonly SemanticTarget[];
}

export interface SemanticCheckoutCompletion {
  complete: boolean;
  hasFilledRequiredTarget: boolean;
  missingRequiredTargets: SemanticTarget[];
}

/**
 * Pure evaluation of an already-normalized semantic state. DOM visibility,
 * same-as-shipping handling, profile mapping and shop-specific decisions must
 * have happened before this function is called.
 *
 * Required identity is always the complete SemanticTarget. An intent alone is
 * never sufficient to decide checkout completion.
 */
export function evaluateSemanticCheckoutCompletion(
  state: SemanticCompletionState
): SemanticCheckoutCompletion {
  const filledKeys = new Set<SemanticTargetKey>(state.filled.map(targetKey));
  const missingKeys = new Set<SemanticTargetKey>(state.missing.map(targetKey));
  const required = new Map<SemanticTargetKey, SemanticTarget>();

  for (const target of state.requiredTargets) {
    required.set(targetKey(target), { ...target });
  }

  const hasFilledRequiredTarget = [...required.keys()].some(key => filledKeys.has(key));
  const missingRequiredTargets = [...required.entries()]
    .filter(([key]) => missingKeys.has(key) || !filledKeys.has(key))
    .map(([, target]) => ({ ...target }));

  return {
    complete: required.size > 0 && hasFilledRequiredTarget && missingRequiredTargets.length === 0,
    hasFilledRequiredTarget,
    missingRequiredTargets
  };
}
