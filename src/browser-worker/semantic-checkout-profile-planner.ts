import type { Page } from "./types";
import type { AresProfile } from "../profiles/models";
import {
  SemanticCheckoutTraceRecorder,
  type SemanticCheckoutBillingMode
} from "./semantic-checkout-observability";
import type { SemanticTarget } from "./semantic-target";
import type { SemanticFieldValueSource } from "./semantic-target-values";
import type { UiInteractionHelper } from "./ui-interaction-helper";
import { SemanticProfileMapper } from "./semantic-profile-mapper";

export interface PlannedSemanticProfileValues extends SemanticFieldValueSource {
  /** Feature policy from the selected profile. Missing/legacy profiles default to enabled. */
  readonly semanticAutofillEnabled: boolean;
  /** PII-safe per-checkout trace recorder shared with SemanticFieldAutofill. */
  readonly semanticCheckoutTrace: SemanticCheckoutTraceRecorder;
}

export interface SemanticCheckoutProfilePlan {
  values: PlannedSemanticProfileValues;
  billingMode: SemanticCheckoutBillingMode;
}

class PlannedProfileValues implements PlannedSemanticProfileValues {
  readonly semanticAutofillEnabled: boolean;

  constructor(
    private readonly mapper: SemanticProfileMapper,
    profile: AresProfile,
    readonly semanticCheckoutTrace: SemanticCheckoutTraceRecorder
  ) {
    this.semanticAutofillEnabled = profile.browser?.kiAutofill !== false;
  }

  valueFor(target: SemanticTarget): string | undefined {
    return this.mapper.valueFor(target);
  }
}

const SAME_AS_SHIPPING_TEXT = /(?:same\s+as\s+(?:shipping|delivery)|billing\s+address\s+(?:is\s+)?same\s+as|rechnungs(?:adresse|anschrift)\s+(?:ist\s+)?(?:gleich|entspricht)\s+(?:der\s+)?liefer(?:adresse|anschrift)|liefer(?:adresse|anschrift)\s+(?:auch|als)\s+rechnungs(?:adresse|anschrift))/i;

export class SemanticCheckoutProfilePlanner {
  constructor(private readonly interactions: UiInteractionHelper) {}

  async prepare(page: Page, profile: AresProfile): Promise<SemanticCheckoutProfilePlan> {
    if (profile.billingAddress) {
      return this.plan(
        new SemanticProfileMapper(profile, { billingMode: "separate-billing-fields" }),
        profile,
        "explicit-billing"
      );
    }

    const preferred = new SemanticProfileMapper(profile, { billingMode: "prefer-same-as-shipping" });
    if (await this.activateSameAsShipping(page)) {
      return this.plan(preferred, profile, "same-as-shipping");
    }

    return this.plan(
      new SemanticProfileMapper(profile, { billingMode: "separate-billing-fields" }),
      profile,
      "separate-billing-fields"
    );
  }

  private plan(
    mapper: SemanticProfileMapper,
    profile: AresProfile,
    billingMode: SemanticCheckoutBillingMode
  ): SemanticCheckoutProfilePlan {
    const semanticCheckoutTrace = new SemanticCheckoutTraceRecorder(billingMode);
    return {
      values: new PlannedProfileValues(mapper, profile, semanticCheckoutTrace),
      billingMode
    };
  }

  private async activateSameAsShipping(page: Page): Promise<boolean> {
    const selector = 'label, button, [role="checkbox"], [role="radio"], input[type="checkbox"], input[type="radio"]';
    const candidates = page.locator(selector);
    // One evaluateAll instead of up to 120 candidate × 3 RPC round-trips: this
    // was the dominant cost between "checkout opened" and "preparing".
    const match = await candidates.evaluateAll((elements, args) => {
      const re = new RegExp(String(args.source), String(args.flags));
      for (let index = 0; index < elements.length && index < 200; index++) {
        const element = elements[index];
        const id = element.id || "";
        const explicitLabel = id
          ? Array.from(document.querySelectorAll("label")).find(label => label.htmlFor === id)?.textContent || ""
          : "";
        const enclosingLabel = element.closest("label")?.textContent || "";
        const text = [
          element.textContent || "",
          element.getAttribute("aria-label") || "",
          element.getAttribute("name") || "",
          element.getAttribute("id") || "",
          explicitLabel,
          enclosingLabel
        ].join(" ").replace(/\s+/g, " ").trim();
        if (!re.test(text)) continue;

        const tag = element.tagName.toLowerCase();
        const type = (element.getAttribute("type") || "").toLowerCase();
        const role = (element.getAttribute("role") || "").toLowerCase();
        let usable = false;
        if (tag === "input" && (type === "checkbox" || type === "radio")) usable = true;
        else if (role === "checkbox" || role === "radio" || tag === "button") usable = true;
        else if (tag === "label") {
          const htmlFor = (element as HTMLLabelElement).htmlFor || element.getAttribute("for") || "";
          usable = Boolean(htmlFor && document.getElementById(htmlFor)) ||
            Boolean(element.querySelector('input[type="checkbox"], input[type="radio"], [role="checkbox"], [role="radio"]'));
        }
        if (!usable) continue;

        let selected = false;
        if (element instanceof HTMLInputElement && (element.type === "checkbox" || element.type === "radio")) {
          selected = element.checked;
        } else if (element instanceof HTMLLabelElement) {
          const target = element.htmlFor
            ? document.getElementById(element.htmlFor)
            : element.querySelector('input[type="checkbox"], input[type="radio"], [role="checkbox"], [role="radio"]');
          if (target instanceof HTMLInputElement) selected = target.checked;
          else selected = target?.getAttribute("aria-checked") === "true";
        } else {
          selected = element.getAttribute("aria-checked") === "true";
        }
        return { index, selected };
      }
      return { index: -1, selected: false };
    }, { source: SAME_AS_SHIPPING_TEXT.source, flags: SAME_AS_SHIPPING_TEXT.flags }).catch(() => ({ index: -1, selected: false }));

    if (match.index < 0) return false;
    if (match.selected) return true;

    const control = candidates.nth(match.index);
    if (!await control.isVisible({ timeout: 200 }).catch(() => false)) return false;
    await this.interactions.click(control, {
      attempts: 2,
      seed: "semantic-billing:same-as-shipping"
    }).catch(() => undefined);
    return control.evaluate(element => {
      if (element instanceof HTMLInputElement) return element.checked;
      const ariaChecked = element.getAttribute("aria-checked");
      if (ariaChecked) return ariaChecked === "true";
      return false;
    }).catch(() => false);
  }
}
