import type { Page } from "./types";
import type { AresProfile } from "../profiles/models";
import { FieldSemanticResolver, OllamaEmbeddingProvider } from "./field-semantic-resolver";
import { SemanticFieldAutofill } from "./semantic-field-autofill";
import { evaluateSemanticCheckoutCompletion } from "./semantic-checkout-completion";
import { SemanticCheckoutProfilePlanner } from "./semantic-checkout-profile-planner";
import { semanticTarget, type FieldIntent, type SemanticTarget } from "./semantic-target";
import { GhostCursorUiInteractionHelper } from "./ui-interaction-helper";

type ConcreteFieldIntent = Exclude<FieldIntent, "unknown">;
type CheckoutTarget = SemanticTarget & { intent: ConcreteFieldIntent };

export interface SemanticCheckoutPreparationResult {
  filled: SemanticTarget[];
  missing: SemanticTarget[];
  writeCounts: Record<string, number>;
  billingMode: "explicit-billing" | "same-as-shipping" | "separate-billing-fields";
  requiredTargetsSatisfied: boolean;
  requiredTargetCount: number;
}

/**
 * Shop-neutral checkout profile preparation using the existing semantic resolver.
 * The resolver implementation remains untouched; this class is only a caller.
 */
export class SemanticCheckoutPreparer {
  private readonly fieldResolver = new FieldSemanticResolver(new OllamaEmbeddingProvider());

  async prepare(page: Page, profile: AresProfile): Promise<SemanticCheckoutPreparationResult> {
    const interactions = new GhostCursorUiInteractionHelper(page);
    const plan = await new SemanticCheckoutProfilePlanner(interactions).prepare(page, profile);
    const autofill = new SemanticFieldAutofill(page, interactions, this.fieldResolver);

    const requiredIntents = new Set<ConcreteFieldIntent>([
      "email",
      "firstName",
      "lastName",
      "address1",
      "city",
      "postalCode"
    ]);

    const requiredTargets = (): SemanticTarget[] => autofill.observedTargets().filter(target =>
      target.intent !== "unknown" && requiredIntents.has(target.intent as ConcreteFieldIntent)
    );

    const fallbackFields: Array<{ target: CheckoutTarget; selectors: string[]; select?: boolean }> = [
      { target: semanticTarget("email", "unknown") as CheckoutTarget, selectors: ['input[name="email"]', 'input[type="email"]', 'input[autocomplete="email"]'] },
      { target: semanticTarget("firstName", "unknown") as CheckoutTarget, selectors: ['input[name="firstName"]', 'input[name*="first_name" i]', 'input[autocomplete="given-name"]'] },
      { target: semanticTarget("lastName", "unknown") as CheckoutTarget, selectors: ['input[name="lastName"]', 'input[name*="last_name" i]', 'input[autocomplete="family-name"]'] },
      { target: semanticTarget("address1", "unknown") as CheckoutTarget, selectors: ['input[name="address1"]', 'input[name*="address1" i]', 'input[autocomplete="address-line1"]'] },
      { target: semanticTarget("address2", "unknown") as CheckoutTarget, selectors: ['input[name="address2"]', 'input[name*="address2" i]', 'input[autocomplete="address-line2"]'] },
      { target: semanticTarget("city", "unknown") as CheckoutTarget, selectors: ['input[name="city"]', 'input[autocomplete="address-level2"]'] },
      { target: semanticTarget("postalCode", "unknown") as CheckoutTarget, selectors: ['input[name="postalCode"]', 'input[name*="postal" i]', 'input[name*="zip" i]', 'input[autocomplete="postal-code"]'] },
      { target: semanticTarget("phone", "unknown") as CheckoutTarget, selectors: ['input[name="phone"]', 'input[type="tel"]', 'input[autocomplete="tel"]'] },
      { target: semanticTarget("countryCode", "unknown") as CheckoutTarget, selectors: ['select[name="countryCode"]', 'select[name*="country" i]'], select: true }
    ];

    for (let attempt = 0; attempt < 6; attempt++) {
      if (attempt > 0) await new Promise(resolve => setTimeout(resolve, 450));
      await autofill.fillSemantic(plan.values).catch(() => undefined);

      for (const fallback of fallbackFields) {
        if (autofill.hasObservedIntent(fallback.target.intent)) continue;
        const value = plan.values.valueFor(fallback.target);
        if (!value?.trim()) continue;

        for (const selector of fallback.selectors) {
          const locator = page.locator(selector).first();
          try {
            const success = fallback.select
              ? await autofill.selectLocator(fallback.target, locator, value)
              : await autofill.fillLocator(fallback.target, locator, value);
            if (success) break;
          } catch {}
        }
      }

      const snapshot = await autofill.result(plan.values);
      const targets = requiredTargets();
      const completion = evaluateSemanticCheckoutCompletion({
        filled: snapshot.filled,
        missing: snapshot.missing,
        requiredTargets: targets
      });
      if (targets.length > 0 && completion.complete) break;
    }

    const result = await autofill.result(plan.values);
    const targets = requiredTargets();
    const completion = evaluateSemanticCheckoutCompletion({
      filled: result.filled,
      missing: result.missing,
      requiredTargets: targets
    });

    return {
      filled: result.filled,
      missing: result.missing,
      writeCounts: result.writeCounts,
      billingMode: plan.billingMode,
      requiredTargetsSatisfied: targets.length > 0 && completion.complete,
      requiredTargetCount: targets.length
    };
  }
}
