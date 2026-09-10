import type { Page } from "../browser-worker/types";
import type { LiveChallengeDetection, LiveChallengeType } from "./types";

export class LiveChallengeDetector {
  /**
   * Prüft einen HTML/URL-Snapshot auf Challenges (wird von den Snapshot-Tests verwendet).
   */
  detectFromSnapshot(url: any = "", html: any = "", title: any = ""): LiveChallengeDetection {
    const safeUrl = typeof url === "string" ? url : String(url ?? "");
    const safeHtml = typeof html === "string" ? html : String(html ?? "");
    const safeTitle = typeof title === "string" ? title : String(title ?? "");

    const lowerUrl = safeUrl.toLowerCase();
    const lowerHtml = safeHtml.toLowerCase();
    const lowerTitle = safeTitle.toLowerCase();

    // 1. Generic Interstitial (Cloudflare "Just a moment...", DataDome, etc.)
    if (
      lowerTitle.includes("just a moment") ||
      lowerTitle.includes("datadome captcha") ||
      lowerUrl.includes("captcha-delivery.com") ||
      lowerHtml.includes("cf-browser-verification") ||
      lowerHtml.includes("challenge-running") ||
      lowerHtml.includes("captcha-delivery.com") ||
      lowerHtml.includes("geo.captcha-delivery.com") ||
      lowerHtml.includes("ct.captcha-delivery.com") ||
      lowerHtml.includes("datadome captcha") ||
      lowerHtml.includes("initialcid=") ||
      lowerHtml.includes("captcha/?initialcid=") ||
      lowerHtml.includes("var dd=") ||
      lowerHtml.includes("var dd =") ||
      (lowerHtml.includes("ray id:") && lowerHtml.includes("cloudflare"))
    ) {
      return {
        detected: true,
        type: "generic-interstitial",
        url: safeUrl,
        title: safeTitle
      };
    }

    // 2. Shopify Queue / Throttle Warteraum
    if (
      lowerUrl.includes("/throttle") ||
      lowerUrl.includes("/queue") ||
      lowerHtml.includes("checkout-queue") ||
      lowerHtml.includes("waiting room")
    ) {
      return {
        detected: true,
        type: "shopify-queue",
        url: safeUrl,
        title: safeTitle
      };
    }

    // 3. Cloudflare Turnstile
    if (
      lowerHtml.includes("cf-turnstile") ||
      lowerHtml.includes("challenges.cloudflare.com") ||
      lowerHtml.includes("turnstile.js") ||
      lowerHtml.includes('name="cf-turnstile-response"')
    ) {
      return {
        detected: true,
        type: "turnstile",
        url: safeUrl,
        title: safeTitle
      };
    }

    // 4. Google reCAPTCHA
    if (
      lowerHtml.includes("g-recaptcha") ||
      lowerHtml.includes("recaptcha/api.js") ||
      lowerHtml.includes('name="g-recaptcha-response"')
    ) {
      return {
        detected: true,
        type: "recaptcha",
        url: safeUrl,
        title: safeTitle
      };
    }

    // 5. hCaptcha
    if (
      lowerHtml.includes("h-captcha") ||
      lowerHtml.includes("hcaptcha.com") ||
      lowerHtml.includes('name="h-captcha-response"')
    ) {
      return {
        detected: true,
        type: "hcaptcha",
        url: safeUrl,
        title: safeTitle
      };
    }

    // 6. Shopify Checkpoint (ohne spezifisches Widget)
    if (
      lowerUrl.includes("/checkpoint") ||
      lowerHtml.includes("form#checkpoint-form") ||
      lowerHtml.includes('action="/checkpoint"')
    ) {
      return {
        detected: true,
        type: "shopify-checkpoint",
        url: safeUrl,
        title: safeTitle
      };
    }

    // Keine Challenge erkannt (reguläre Seite)
    return {
      detected: false,
      url: safeUrl,
      title: safeTitle
    };
  }

  /**
   * Prüft eine aktive ARES-Page im Browser oder den Mock im Test.
   */
  async detect(page: Page): Promise<LiveChallengeDetection> {
    if (!page || (typeof page.isClosed === "function" && page.isClosed())) {
      return { detected: false, url: "" };
    }

    const url = typeof page.url === "function" ? page.url() : "";
    let title = "";
    if (typeof page.title === "function") {
      title = (await page.title().catch(() => "")) || "";
    }

    // 1. Direkte Auswertung über page.evaluate (funktioniert im echten Browser und im Test-Mock)
    if (typeof (page as any).evaluate === "function") {
      try {
        const evalResult = await (page as any).evaluate(() => {
          const hasTurnstile = Boolean(
            document.querySelector('input[name="cf-turnstile-response"]') ||
            document.querySelector('.cf-turnstile') ||
            document.querySelector('iframe[src*="challenges.cloudflare.com"]') ||
            document.querySelector('iframe[src*="turnstile"]')
          );
          if (hasTurnstile) return "turnstile";

          const hasRecaptcha = Boolean(
            document.querySelector('textarea[name="g-recaptcha-response"]') ||
            document.querySelector('.g-recaptcha') ||
            document.querySelector('iframe[src*="google.com/recaptcha"]')
          );
          if (hasRecaptcha) return "recaptcha";

          const hasHcaptcha = Boolean(
            document.querySelector('textarea[name="h-captcha-response"]') ||
            document.querySelector('.h-captcha') ||
            document.querySelector('iframe[src*="hcaptcha.com"]')
          );
          if (hasHcaptcha) return "hcaptcha";

          const hasDataDome = Boolean(
            document.querySelector('iframe[title*="DataDome" i]') ||
            document.querySelector('iframe[src*="captcha-delivery.com"]') ||
            document.querySelector('script[src*="captcha-delivery.com"]') ||
            document.documentElement.innerHTML.toLowerCase().includes("captcha-delivery.com")
          );
          if (hasDataDome) return "generic-interstitial";

          if (document.title.toLowerCase().includes("just a moment") || document.querySelector("#challenge-running")) {
            return "generic-interstitial";
          }

          return null;
        });

        // Wenn der Mock direkt einen Typ (z. B. "turnstile") zurückgibt:
        if (typeof evalResult === "string" && evalResult) {
          return {
            detected: true,
            type: evalResult as LiveChallengeType,
            url,
            title
          };
        }

        // Wenn der Mock ein Objekt zurückgibt (z. B. { type: "turnstile", detected: true }):
        if (evalResult && typeof evalResult === "object") {
          const detectedType = (evalResult.type || (evalResult.turnstile ? "turnstile" : undefined)) as LiveChallengeType;
          return {
            detected: evalResult.detected ?? Boolean(detectedType),
            type: detectedType,
            url: evalResult.url || url,
            title: evalResult.title || title
          };
        }
      } catch {}
    }

    // 2. Fallback über HTML Content
    let html = "";
    if (typeof page["content"] === "function") {
      try {
        const contentVal = await page["content"]();
        if (typeof contentVal === "string") {
          html = contentVal;
        }
      } catch {}
    }

    return this.detectFromSnapshot(url, html, title);
  }
}

export const defaultDetector = new LiveChallengeDetector();

export async function detectLiveChallenge(page: Page): Promise<LiveChallengeDetection> {
  return defaultDetector.detect(page);
}

export function detectFromHtml(html: string, url: string = "", title: string = ""): LiveChallengeDetection {
  return defaultDetector.detectFromSnapshot(url, html, title);
}
