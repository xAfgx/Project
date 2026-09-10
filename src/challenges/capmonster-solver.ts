import {
  CapMonsterCloudClientFactory,
  ClientOptions,
  TurnstileRequest,
  RecaptchaV2Request,
  HCaptchaRequest,
} from "@zennolab_com/capmonstercloud-client";
import type { Page } from "../browser-worker/types";

export interface SolverProxyOptions {
  server: string;
  username?: string;
  password?: string;
}

export class CapMonsterSolver {
  private client;

  constructor(apiKey: string) {
    this.client = CapMonsterCloudClientFactory.Create(
      new ClientOptions({ clientKey: apiKey })
    );
  }

  /**
   * Löst Turnstile, reCAPTCHA oder hCaptcha (inkl. Tier- und Objekterkennung) via CapMonster Cloud.
   */
  async solve(
    type: string,
    websiteUrl: string,
    websiteKey: string,
    proxy?: SolverProxyOptions
  ): Promise<string> {
    let proxyConfig: any = {};

    if (proxy && proxy.server) {
      try {
        const parsed = new URL(proxy.server);
        proxyConfig = {
          proxyType: parsed.protocol.replace(":", "") as "http" | "https" | "socks5",
          proxyAddress: parsed.hostname,
          proxyPort: parseInt(parsed.port, 10),
          proxyLogin: proxy.username || parsed.username || undefined,
          proxyPassword: proxy.password || parsed.password || undefined,
        };
      } catch {}
    }

    // 1. Cloudflare Turnstile
    if (type === "turnstile" || type === "shopify-checkpoint") {
      const task = new TurnstileRequest({
        websiteURL: websiteUrl,
        websiteKey: websiteKey,
        ...proxyConfig,
      });

      const result = await this.client.Solve(task);
      if (!result.solution?.token) {
        throw new Error("CapMonster hat kein Turnstile-Token zurückgegeben.");
      }
      return result.solution.token;
    }

    // 2. Google reCAPTCHA
    if (type === "recaptcha") {
      const task = new RecaptchaV2Request({
        websiteURL: websiteUrl,
        websiteKey: websiteKey,
        ...proxyConfig,
      });

      const result = await this.client.Solve(task);
      const token = (result.solution as any)?.gRecaptchaResponse || (result.solution as any)?.token;
      if (!token) {
        throw new Error("CapMonster hat kein reCAPTCHA-Token zurückgegeben.");
      }
      return token;
    }

    // 3. 🚀 hCaptcha (Pokémon Center Tier- und Bilderrätsel)
    if (type === "hcaptcha") {
      const task = new HCaptchaRequest({
        websiteURL: websiteUrl,
        websiteKey: websiteKey,
        ...proxyConfig,
      });

      const result = await this.client.Solve(task);
      const token = (result.solution as any)?.gRecaptchaResponse || (result.solution as any)?.token;
      if (!token) {
        throw new Error("CapMonster hat kein hCaptcha-Token zurückgegeben.");
      }
      return token;
    }

    throw new Error(`Nicht unterstützter Captcha-Typ: ${type}`);
  }

  async injectAndSubmit(page: Page, type: string, token: string): Promise<void> {
    await page.evaluate(({ type, token }) => {
      // 1. Turnstile Input
      if (type === "turnstile" || type === "shopify-checkpoint") {
        const input = document.querySelector('input[name="cf-turnstile-response"]') as HTMLInputElement;
        if (input) {
          input.value = token;
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.dispatchEvent(new Event("change", { bubbles: true }));
        }
      } 
      // 2. reCAPTCHA Textarea
      else if (type === "recaptcha") {
        const input = document.querySelector('textarea[name="g-recaptcha-response"]') as HTMLTextAreaElement;
        if (input) {
          input.value = token;
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.dispatchEvent(new Event("change", { bubbles: true }));
        }
      } 
      // 3. 🚀 hCaptcha Response Feld
      else if (type === "hcaptcha") {
        const hInput = document.querySelector('textarea[name="h-captcha-response"], input[name="h-captcha-response"]') as HTMLTextAreaElement | HTMLInputElement;
        if (hInput) {
          hInput.value = token;
          hInput.dispatchEvent(new Event("input", { bubbles: true }));
          hInput.dispatchEvent(new Event("change", { bubbles: true }));
        }

        // Viele Seiten erwarten das Token zusätzlich im g-recaptcha Feld
        const gInput = document.querySelector('textarea[name="g-recaptcha-response"]') as HTMLTextAreaElement;
        if (gInput && !gInput.value) {
          gInput.value = token;
          gInput.dispatchEvent(new Event("input", { bubbles: true }));
          gInput.dispatchEvent(new Event("change", { bubbles: true }));
        }
      }

      // Checkpoint-Formular absenden
      const form =
        document.querySelector("form#checkpoint-form") ||
        document.querySelector('form[action*="checkpoint"]') ||
        document.querySelector('form:has([name="h-captcha-response"])') ||
        document.querySelector('form:has([name="cf-turnstile-response"])');

      if (form && form instanceof HTMLFormElement) {
        form.submit();
      }
    }, { type, token });

    const submitBtn = page.locator('form#checkpoint-form button[type="submit"], input[type="submit"], button#submit').first();
    if (await submitBtn.isVisible().catch(() => false)) {
      await submitBtn.click();
    }
  }
}
