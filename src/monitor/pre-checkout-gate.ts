import type { CommerceShop } from "../commerce/platforms";
import type { Task } from "../models";
import type { PreCheckoutGateEvent } from "./early-gate";

/**
 * Compatibility contract for CommerceMonitorService.
 *
 * Early-Gate monitor tasks are routed to BrowserGateMonitorExecutor before
 * CommerceMonitorService is selected. The old standalone HTTP gate probe was
 * therefore unreachable in the production Early-Gate path and, more
 * importantly, did not share the SeleniumBase lane's proxy/profile/session.
 */
export interface PreCheckoutGate {
  evaluate(task: Task, shop: CommerceShop, signal?: AbortSignal): Promise<PreCheckoutGateEvent | undefined>;
}

/**
 * Legacy compatibility guard.
 *
 * Keep the symbol temporarily so older composition/tests fail explicitly
 * instead of silently falling back to a second HTTP-based gate detector.
 * Production Early-Gate monitoring must run through BrowserGateMonitorExecutor.
 */
export class PassiveHttpPreCheckoutGate implements PreCheckoutGate {
  async evaluate(_task: Task, _shop: CommerceShop, signal?: AbortSignal): Promise<PreCheckoutGateEvent | undefined> {
    if (signal?.aborted) return undefined;
    throw new Error(
      "PassiveHttpPreCheckoutGate wurde entfernt. Early-Gate-Monitoring muss über BrowserGateMonitorExecutor/SeleniumBase laufen."
    );
  }
}
