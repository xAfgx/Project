"""Browser-side bridge for the external captcha providers.

Detects the active challenge (reCAPTCHA / hCaptcha), requests a token from the
configured provider (see captcha_api_provider) and injects it into the page.
Everything is best-effort: failures are reported as data and never raised.
"""

from __future__ import annotations

import base64
import time
from typing import Any, Dict, Iterable, List

from captcha_api_provider import captcha_mode, classifier_available, provider_available, solve_token, classify_grid

_DETECT_SCRIPT = r"""
(() => {
  const result = { kind: "", sitekey: "" };
  const explicit = document.querySelector('[data-sitekey]');
  if (explicit) {
    const sitekey = explicit.getAttribute('data-sitekey') || '';
    const marker = String(explicit.className || '') + ' ' + String(explicit.id || '');
    if (sitekey && /g-recaptcha|recaptcha/i.test(marker)) { result.kind = 'recaptcha'; result.sitekey = sitekey; }
    else if (sitekey && /h-captcha|hcaptcha/i.test(marker)) { result.kind = 'hcaptcha'; result.sitekey = sitekey; }
  }
  if (!result.sitekey) {
    for (const frame of document.querySelectorAll('iframe')) {
      const src = String(frame.getAttribute('src') || '');
      if (!src) continue;
      const recaptchaMatch = src.match(/[?&]k=([^&]+)/);
      if (recaptchaMatch && /recaptcha/i.test(src)) {
        result.kind = 'recaptcha';
        result.sitekey = decodeURIComponent(recaptchaMatch[1]);
        break;
      }
      const hcaptchaMatch = src.match(/[?&]sitekey=([^&]+)/);
      if (hcaptchaMatch && /hcaptcha/i.test(src)) {
        result.kind = 'hcaptcha';
        result.sitekey = decodeURIComponent(hcaptchaMatch[1]);
        break;
      }
    }
  }
  return result;
})()
"""

_INJECT_SCRIPT = r"""
(() => {
  const token = String(arguments[0] || '');
  if (!token) return false;
  const setValue = (el) => {
    try {
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
      if (descriptor && descriptor.set) descriptor.set.call(el, token); else el.value = token;
      el.innerHTML = token;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    } catch (_) {}
  };
  const fields = document.querySelectorAll(
    'textarea[name="g-recaptcha-response"], textarea[name="h-captcha-response"], ' +
    'input[name="g-recaptcha-response"], input[name="h-captcha-response"], #g-recaptcha-response'
  );
  fields.forEach(setValue);
  try {
    const cfg = window.___grecaptcha_cfg;
    if (cfg && cfg.clients) {
      for (const clientKey of Object.keys(cfg.clients)) {
        const client = cfg.clients[clientKey];
        for (const key of Object.keys(client || {})) {
          const candidate = client[key];
          if (candidate && typeof candidate.callback === 'function') {
            try { candidate.callback(token); } catch (_) {}
          }
        }
      }
    }
  } catch (_) {}
  try {
    if (window.hcaptcha && typeof window.hcaptcha.getResponse === 'function') {
      const anyWindow = window;
      if (typeof anyWindow.__aresHcaptchaCallback === 'function') anyWindow.__aresHcaptchaCallback(token);
    }
  } catch (_) {}
  return fields.length > 0;
})()
"""


class CaptchaApiSolver:
    """Solves the current challenge through the configured provider."""

    def __init__(self, sb: Any, trace: Any = None) -> None:
        self._sb = sb
        self._trace = trace

    def available(self) -> bool:
        try:
            return provider_available() or classifier_available()
        except Exception:
            return False

    def can_classify(self) -> bool:
        try:
            return classifier_available()
        except Exception:
            return False

    def classify(self, sources: Iterable[str], instruction: str) -> Dict[str, Any]:
        """Classify an image grid through the configured provider.

        Returns {ok, indexes, provider, error}. Tile sources may be data URLs or
        file paths; they are converted to raw base64 for the provider API.
        """
        images: List[str] = []
        for source in sources:
            value = str(source or "").strip()
            if not value:
                continue
            if value.startswith("data:"):
                payload = value.split(",", 1)[1] if "," in value else ""
                if payload:
                    images.append(payload)
                continue
            try:
                with open(value, "rb") as handle:
                    images.append(base64.b64encode(handle.read()).decode("ascii"))
            except Exception:
                continue
        if not images:
            return {"ok": False, "indexes": [], "provider": "", "error": "no-tile-images"}
        try:
            url = str(self._sb.get_current_url() or "")
        except Exception:
            url = ""
        outcome = classify_grid(images=images, question=instruction, url=url)
        indexes = [int(value) for value in (outcome.get("indexes") or [])]
        return {
            "ok": bool(indexes),
            "indexes": indexes,
            "provider": str(outcome.get("provider") or ""),
            "error": str(outcome.get("error") or ""),
        }

    def mode(self) -> str:
        try:
            return captcha_mode()
        except Exception:
            return "siglip"

    def challenge_info(self) -> Dict[str, str]:
        try:
            value = self._sb.execute_script(_DETECT_SCRIPT)
            if isinstance(value, dict):
                return {
                    "kind": str(value.get("kind") or ""),
                    "sitekey": str(value.get("sitekey") or ""),
                }
        except Exception:
            pass
        return {"kind": "", "sitekey": ""}

    def solve(self, timeout_seconds: float = 120.0, attempts: int = 3) -> Dict[str, Any]:
        """Request/inject a token; retry a few times for more chances.

        API-only mode gets multiple fresh tokens because the page may need the
        challenge frame to settle before a token is accepted.
        """
        total = max(1, min(6, int(attempts)))
        last: Dict[str, Any] = {"solved": False, "reason": "not-started"}
        for attempt in range(1, total + 1):
            result = self._solve_once(timeout_seconds=timeout_seconds)
            result["attempt"] = attempt
            if result.get("solved"):
                return result
            last = result
            if str(result.get("reason") or "") == "challenge-not-detected":
                break
            if attempt < total:
                time.sleep(1.5)
        return last

    def _solve_once(self, timeout_seconds: float = 120.0) -> Dict[str, Any]:
        try:
            info = self.challenge_info()
        except Exception as exc:
            return {"solved": False, "reason": f"detect-error:{type(exc).__name__}"}
        kind = info.get("kind") or ""
        sitekey = info.get("sitekey") or ""
        if not kind or not sitekey:
            return {"solved": False, "reason": "challenge-not-detected"}
        try:
            url = str(self._sb.get_current_url() or "")
        except Exception:
            url = ""
        outcome = solve_token(kind=kind, sitekey=sitekey, url=url, timeout_seconds=timeout_seconds)
        token = str(outcome.get("token") or "")
        provider = str(outcome.get("provider") or "")
        if not token:
            return {"solved": False, "provider": provider, "reason": str(outcome.get("error") or "no-token")}
        injected = False
        try:
            injected = bool(self._sb.execute_script(_INJECT_SCRIPT, token))
        except Exception:
            injected = False
        if not injected:
            return {"solved": False, "provider": provider, "reason": "injection-failed"}
        solved = self._wait_cleared(timeout_seconds=20.0)
        return {
            "solved": solved,
            "provider": provider,
            "reason": "" if solved else "challenge-still-present",
        }

    def _wait_cleared(self, *, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + max(2.0, min(60.0, timeout_seconds))
        while time.monotonic() < deadline:
            info = self.challenge_info()
            if not info.get("kind"):
                return True
            time.sleep(1.0)
        return False
