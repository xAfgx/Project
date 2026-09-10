import type { Page } from "./types";

const SUCCESS_URL = /(?:\/thank[_-]?you(?:[/?#]|$)|\/order(?:s)?\/(?:confirmation|confirmed|complete|success)(?:[/?#]|$)|\/checkout\/(?:thank[_-]?you|confirmation|success)(?:[/?#]|$))/i;
const SUCCESS_TEXT = [
  /thank you for (?:your )?(?:order|purchase)/i,
  /(?:your )?order (?:has been )?(?:confirmed|received|completed)/i,
  /we(?:'ve| have) received your order/i,
  /vielen dank für (?:deine|ihre) bestellung/i,
  /bestellung (?:wurde )?(?:bestätigt|bestaetigt|eingegangen|abgeschlossen)/i
];

export interface CheckoutOutcomeObservation {
  confirmed: boolean;
  source: "url" | "text" | "none";
}

/** Strong, generic post-submit confirmation observer. A click/navigation alone is never success. */
export async function observeCheckoutOutcome(page: Page): Promise<CheckoutOutcomeObservation> {
  const url = String(page.url() || "");
  if (SUCCESS_URL.test(url)) return { confirmed: true, source: "url" };

  let title = "";
  let body = "";
  try { title = await page.title(); } catch {}
  try { body = await page.locator("body").innerText({ timeout: 750 }); } catch {}
  const text = `${title}\n${body}`.replace(/\s+/g, " ").slice(0, 40_000);
  if (SUCCESS_TEXT.some(pattern => pattern.test(text))) {
    return { confirmed: true, source: "text" };
  }
  return { confirmed: false, source: "none" };
}
