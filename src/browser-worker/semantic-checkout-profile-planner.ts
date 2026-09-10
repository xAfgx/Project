import type { Locator, Page } from "./types";
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
    const candidates = page.locator('label, button, [role="checkbox"], [role="radio"], input[type="checkbox"], input[type="radio"]');
    const count = Math.min(await candidates.count().catch(() => 0), 120);

    for (let index = 0; index < count; index++) {
      const candidate = candidates.nth(index);
      const text = await this.candidateText(candidate).catch(() => "");
      if (!SAME_AS_SHIPPING_TEXT.test(text)) continue;

      const control = await this.resolveControl(candidate);
      if (!control || !await control.isVisible({ timeout: 120 }).catch(() => false)) continue;
      if (await this.isSelected(control)) return true;

      await this.interactions.click(control, {
        attempts: 2,
        seed: "semantic-billing:same-as-shipping"
      }).catch(() => undefined);
      if (await this.isSelected(control)) return true;
    }

    return false;
  }

  private async candidateText(locator: Locator): Promise<string> {
    return locator.evaluate(element => {
      const input = element as HTMLInputElement;
      const id = input.id || "";
      const explicitLabel = id
        ? Array.from(document.querySelectorAll("label")).find(label => label.htmlFor === id)?.textContent || ""
        : "";
      const enclosingLabel = element.closest("label")?.textContent || "";
      return [
        element.textContent || "",
        element.getAttribute("aria-label") || "",
        element.getAttribute("name") || "",
        element.getAttribute("id") || "",
        explicitLabel,
        enclosingLabel
      ].join(" ").replace(/\s+/g, " ").trim();
    });
  }

  private async resolveControl(candidate: Locator): Promise<Locator | undefined> {
    const usable = await candidate.evaluate(element => {
      const tag = element.tagName.toLowerCase();
      const type = (element.getAttribute("type") || "").toLowerCase();
      const role = (element.getAttribute("role") || "").toLowerCase();
      if (tag === "input" && (type === "checkbox" || type === "radio")) return true;
      if (role === "checkbox" || role === "radio" || tag === "button") return true;
      if (tag !== "label") return false;

      const htmlFor = (element as HTMLLabelElement).htmlFor || element.getAttribute("for") || "";
      if (htmlFor && document.getElementById(htmlFor)) return true;
      return Boolean(element.querySelector('input[type="checkbox"], input[type="radio"], [role="checkbox"], [role="radio"]'));
    }).catch(() => false);
    return usable ? candidate : undefined;
  }

  private async isSelected(control: Locator): Promise<boolean> {
    return control.evaluate(element => {
      const ariaChecked = element.getAttribute("aria-checked");
      if (ariaChecked === "true") return true;
      if (ariaChecked === "false") return false;

      if (element instanceof HTMLInputElement && (element.type === "checkbox" || element.type === "radio")) {
        return element.checked;
      }

      if (element instanceof HTMLLabelElement) {
        const explicit = element.htmlFor ? document.getElementById(element.htmlFor) : null;
        const nested = element.querySelector('input[type="checkbox"], input[type="radio"], [role="checkbox"], [role="radio"]');
        const target = explicit || nested;
        if (target instanceof HTMLInputElement) return target.checked;
        return target?.getAttribute("aria-checked") === "true";
      }

      return false;
    }).catch(() => false);
  }
}
