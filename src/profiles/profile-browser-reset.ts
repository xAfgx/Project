import type { AresProfile } from "./models";

/**
 * Clears browser-session identity that is intentionally reset with profile browser data.
 * Address, proxy selection, payment preference and other ARES profile data stay untouched.
 */
export function clearProfileBrowserUserAgent(profile: AresProfile): AresProfile {
  const browser = { ...(profile.browser ?? {}) };
  delete browser.userAgent;
  return { ...profile, browser };
}
