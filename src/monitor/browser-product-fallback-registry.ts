import type { BrowserProductFallback } from "./browser-product-fallback";

let defaultBrowserProductFallback: BrowserProductFallback | undefined;

export function setDefaultBrowserProductFallback(fallback: BrowserProductFallback | undefined): void {
  defaultBrowserProductFallback = fallback;
}

export function getDefaultBrowserProductFallback(): BrowserProductFallback | undefined {
  return defaultBrowserProductFallback;
}
