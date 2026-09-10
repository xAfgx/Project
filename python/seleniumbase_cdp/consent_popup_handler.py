from __future__ import annotations

import json
from typing import Any, Dict, Iterable


_DIALOG_SELECTORS = (
    '[role="dialog"]',
    '[aria-modal="true"]',
    '[class*="cookie" i]',
    '[id*="cookie" i]',
    '[class*="consent" i]',
    '[id*="consent" i]',
    '[class*="modal" i]',
)
_BUTTON_SELECTORS = (
    'button',
    'input[type="submit"]',
    'input[type="button"]',
    '[role="button"]',
    'a[href]',
)
_ACCEPT_TEXT = (
    'alles akzeptieren',
    'alle akzeptieren',
    'alles zulassen',
    'alle zulassen',
    'accept all',
    'allow all',
    'akzeptieren',
    'accept',
    'zustimmen',
    'i agree',
    'ok',
    'weiter',
    'continue',
    'akzeptieren und fortfahren',
    'akzeptieren und weiter',
    'alle cookies akzeptieren',
    'cookies akzeptieren',
    'alle cookies zulassen',
    'cookies zulassen',
    'alle erlauben',
    'erlauben',
    'alle cookies erlauben',
    'cookies erlauben',
    'verstanden',
    'einverstanden',
    'stimme zu',
    'zustimmen und weiter',
    'ok, verstanden',
    'akzeptieren & fortfahren',
    'schließen',
    'schliessen',
    'close',
    'dismiss',
    'ausblenden',
    'ablehnen',
    'nur notwendige',
    'nur essentielle',
    'notwendige cookies',
)
_PROGRESS_TEXT = (
    'bestätigen und weiter',
    'weiter',
    'fortfahren',
    'continue',
    'ok',
    'weiter zur kasse',
    'zur kasse',
    'checkout',
    'go to checkout',
    'continue to checkout',
    'proceed to checkout',
    'weiter zum checkout',
    'zum checkout',
    'weiter zur lieferung',
    'continue to shipping',
    'weiter zur zahlung',
    'continue to payment',
)
_CHECKOUT_TEXT = (
    'weiter zur kasse',
    'zur kasse',
    'checkout',
    'go to checkout',
    'continue to checkout',
    'proceed to checkout',
    'weiter zum checkout',
    'zum checkout',
    'weiter zur lieferung',
    'weiter zur zahlung',
    'continue to shipping',
    'continue to payment',
)


class ConsentPopupHandler:
    """Dismiss explicit popups and advance only reversible controls with CDP mouse clicks."""

    def __init__(self, seleniumbase_cdp: Any) -> None:
        self._sb = seleniumbase_cdp

    def dismiss_once(self) -> Dict[str, Any]:
        cdp = getattr(self._sb, 'cdp', self._sb)
        if cdp is None:
            return {'dismissed': False, 'reason': 'cdp-unavailable'}

        # Always attempt the click paths. The preflight candidate gate proved
        # too fragile (returned "no-explicit-consent-control" even when the
        # banner/button was present, e.g. late-loading CMP banners), which
        # blocked the click entirely. The click paths already no-op when there
        # is nothing to click, so the gate added no safety, only false negatives.
        for dialog_selector in _DIALOG_SELECTORS:
            try:
                dialogs = list(cdp.find_elements(dialog_selector) or [])
            except Exception:
                dialogs = []
            for dialog in dialogs:
                result = self._click_in_root(dialog)
                if result.get('dismissed'):
                    return result

        result = self._click_in_root(cdp, fallback=True)
        if result.get('dismissed'):
            return result

        # Shadow-DOM banners (Usercentrics/Quantcast) hide the accept button
        # behind a shadow root, which find_elements does not pierce. This JS
        # fallback clicks the exact accept element via el.click() (reliable, no
        # coordinate misfires). It runs only after mouse_click() found nothing.
        js_click = self._click_shadow_js()
        if js_click.get('dismissed'):
            return js_click

        # Cross-origin CMP banners (e.g. Usercentrics in usercentrics.eu) render
        # the accept button inside a cross-origin iframe that plain JS cannot
        # pierce. The OOPIF registry can evaluate JS in those frames directly.
        oopif_click = self._click_oopif()
        if oopif_click.get('dismissed'):
            return oopif_click

        # Same-process frames fallback (recurse, bounded depth).
        frame_click = self._click_in_frames(cdp)
        if frame_click.get('dismissed'):
            return frame_click

        return {'dismissed': False, 'reason': 'no-explicit-consent-control'}

    def _click_oopif(self) -> Dict[str, Any]:
        discover = getattr(self._sb, "ares_oopif_discover", None)
        evaluate = getattr(self._sb, "ares_oopif_evaluate", None)
        if not callable(discover) or not callable(evaluate):
            return {'dismissed': False}
        try:
            entries = list(discover() or [])
        except Exception:
            return {'dismissed': False}
        script = self._shadow_click_script()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            frame_path = [str(value) for value in entry.get("path") or [] if str(value)]
            if not frame_path:
                continue
            try:
                value = evaluate(frame_path, script, [])
            except Exception:
                continue
            if value is True:
                return {'dismissed': True, 'mode': 'oopif-js-click', 'frame': frame_path}
        return {'dismissed': False}

    def _click_in_frames(self, cdp: Any, _depth: int = 0) -> Dict[str, Any]:
        if _depth > 3:
            return {'dismissed': False}
        try:
            frames = list(cdp.find_elements('iframe') or [])
        except Exception:
            return {'dismissed': False}
        for frame in frames:
            result = self._click_in_root(frame, fallback=True)
            if result.get('dismissed'):
                return {**result, 'scope': 'iframe'}
            nested = self._click_in_frames(frame, _depth + 1)
            if nested.get('dismissed'):
                return nested
        return {'dismissed': False}

    def _click_shadow_accept(self) -> Dict[str, Any]:
        """Find an accept/consent button spanning Shadow DOM roots and click it."""
        script = self._shadow_accept_script()
        try:
            evaluator = getattr(self._sb, 'evaluate', None)
            value = evaluator(script) if callable(evaluator) else self._sb.execute_script(f"return {script};")
        except Exception:
            return {'dismissed': False}
        if not isinstance(value, dict) or not isinstance(value.get('rect'), dict):
            return {'dismissed': False}
        rect = value['rect']
        try:
            x = float(rect.get('x') or 0.0) + float(rect.get('width') or 0.0) / 2.0
            y = float(rect.get('y') or 0.0) + float(rect.get('height') or 0.0) / 2.0
        except (TypeError, ValueError):
            return {'dismissed': False}
        if not (x >= 0 and y >= 0):
            return {'dismissed': False}
        if self._cdp_click(x, y):
            return {'dismissed': True, 'mode': 'shadow-cdp', 'x': round(x, 2), 'y': round(y, 2)}
        return {'dismissed': False}

    def _shadow_accept_script(self) -> str:
        candidates = json.dumps([str(value).strip().lower() for value in _ACCEPT_TEXT if str(value).strip()])
        keywords = json.dumps(list(self._ACCEPT_KEYWORDS))
        rejects = json.dumps(list(self._REJECT_KEYWORDS))
        return r"""
(() => {
  const candidates = """ + candidates + """;
  const keywords = """ + keywords + """;
  const rejects = """ + rejects + """;
  const norm = v => String(v || '').trim().toLowerCase().replace(/\s+/g, ' ');
  const matches = raw => {
    const text = norm(raw);
    if (!text) return false;
    if (candidates.some(c => { const n = norm(c); if (!n) return false; return text === n || (n.includes(' ') && n.length > 8 && text.includes(n)); })) return true;
    if (rejects.some(r => norm(r) && text.includes(norm(r)))) return false;
    return keywords.some(k => norm(k) && text.includes(norm(k)));
  };
  const textOf = el => el?.innerText || el?.textContent || el?.getAttribute?.('value') || el?.getAttribute?.('aria-label') || el?.getAttribute?.('title') || '';
  const visible = el => {
    if (!el?.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width >= 20 && r.height >= 20 && s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
  };
  // Stable CMP accept-button identities first (OneTrust, Usercentrics, generic).
  const stable = [
    'button#onetrust-accept-btn-handler',
    'button[data-testid="uc-accept-all-button"]',
    'button[data-testid="uc-accept-all"]',
    '[data-testid="accept-all" i]',
    '[id*="accept" i] button, button[id*="accept" i]',
    '[data-testid*="accept" i]',
    '[class*="accept-all" i], [id*="accept-all" i]',
  ];
  const seek = node => {
    if (!node) return null;
    for (const selector of stable) {
      try {
        const found = node.querySelector(selector);
        if (found && visible(found) && matches(textOf(found))) {
          const r = found.getBoundingClientRect();
          return { rect: { x: r.x, y: r.y, width: r.width, height: r.height } };
        }
      } catch (_) {}
    }
    for (const selector of ['button', 'input[type="submit"]', 'input[type="button"]', '[role="button"]', 'a[href]']) {
      try {
        for (const el of node.querySelectorAll(selector) || []) {
          if (visible(el) && matches(textOf(el))) {
            const r = el.getBoundingClientRect();
            return { rect: { x: r.x, y: r.y, width: r.width, height: r.height } };
          }
        }
      } catch (_) {}
    }
    try {
      for (const el of node.querySelectorAll('*') || []) {
        if (el.shadowRoot) { const found = seek(el.shadowRoot); if (found) return found; }
      }
    } catch (_) {}
    return null;
  };
  return seek(document) || null;
})()
"""

    def _cdp_click(self, x: float, y: float) -> bool:
        try:
            import mycdp
            from mycdp import input_ as cdp_input
        except Exception:
            return False
        tab = getattr(self._sb, 'get_active_tab', None)
        loop = getattr(self._sb, 'get_event_loop', None)
        if not callable(tab) or not callable(loop):
            return False
        try:
            active = tab()
            event_loop = loop()
            button = cdp_input.MouseButton("left")
            event_loop.run_until_complete(active.send(cdp_input.dispatch_mouse_event("mouseMoved", x=x, y=y, button=button, buttons=0, pointer_type="mouse")))
            event_loop.run_until_complete(active.send(cdp_input.dispatch_mouse_event("mousePressed", x=x, y=y, button=button, buttons=1, click_count=1, pointer_type="mouse")))
            event_loop.run_until_complete(active.send(cdp_input.dispatch_mouse_event("mouseReleased", x=x, y=y, button=button, buttons=0, click_count=1, pointer_type="mouse")))
            return True
        except Exception:
            return False

    def _click_shadow_js(self) -> Dict[str, Any]:
        script = self._shadow_click_script()
        try:
            evaluator = getattr(self._sb, 'evaluate', None)
            value = evaluator(script) if callable(evaluator) else self._sb.execute_script(f"return {script};")
        except Exception:
            return {'dismissed': False}
        if not isinstance(value, dict) or not isinstance(value.get('x'), (int, float)):
            return {'dismissed': False}
        try:
            x = float(value['x']) + float(value['w']) / 2.0
            y = float(value['y']) + float(value['h']) / 2.0
        except (TypeError, ValueError):
            return {'dismissed': False}
        if self._cdp_click(x, y):
            return {'dismissed': True, 'mode': 'shadow-rect-cdp-click'}
        return {'dismissed': False}

    def _shadow_click_script(self) -> str:
        candidates = json.dumps([str(value).strip().lower() for value in _ACCEPT_TEXT if str(value).strip()])
        keywords = json.dumps(list(self._ACCEPT_KEYWORDS))
        rejects = json.dumps(list(self._REJECT_KEYWORDS))
        return r"""
(() => {
  const candidates = """ + candidates + """;
  const keywords = """ + keywords + """;
  const rejects = """ + rejects + """;
  const norm = v => String(v || '').trim().toLowerCase().replace(/\s+/g, ' ');
  const matches = raw => {
    const text = norm(raw);
    if (!text) return false;
    if (candidates.some(c => { const n = norm(c); if (!n) return false; return text === n || (n.includes(' ') && n.length > 8 && text.includes(n)); })) return true;
    if (rejects.some(r => norm(r) && text.includes(norm(r)))) return false;
    return keywords.some(k => norm(k) && text.includes(norm(k)));
  };
  const textOf = el => el?.innerText || el?.textContent || el?.getAttribute?.('value') || el?.getAttribute?.('aria-label') || el?.getAttribute?.('title') || '';
  const visible = el => {
    if (!el?.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width >= 20 && r.height >= 20 && s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
  };
  const stable = [
    'button#onetrust-accept-btn-handler',
    'button[data-testid="uc-accept-all-button"]',
    'button[data-testid="uc-accept-all"]',
    '[data-testid="accept-all" i]',
    '[id*="accept" i] button, button[id*="accept" i]',
    '[data-testid*="accept" i]',
  ];
  const seek = node => {
    if (!node) return null;
    for (const selector of stable) {
      try {
        const found = node.querySelector(selector);
        if (found && visible(found) && matches(textOf(found))) { const r = found.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}; }
      } catch (_) {}
    }
    for (const selector of ['button', 'input[type="submit"]', 'input[type="button"]', '[role="button"]', 'a[href]']) {
      try {
        for (const el of node.querySelectorAll(selector) || []) {
          if (visible(el) && matches(textOf(el))) { const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}; }
        }
      } catch (_) {}
    }
    try {
      for (const el of node.querySelectorAll('iframe') || []) {
        try { if (el.contentDocument) { const found = seek(el.contentDocument); if (found) return found; } } catch (_) {}
      }
    } catch (_) {}
    try {
      for (const el of node.querySelectorAll('*') || []) {
        if (el.shadowRoot) { const found = seek(el.shadowRoot); if (found) return found; }
      }
    } catch (_) {}
    return null;
  };
  return seek(document);
})()
"""

    def advance_progress_once(self) -> Dict[str, Any]:
        """Advance only conservative confirm/continue/pre-payment checkout controls."""
        return self._advance_once(_PROGRESS_TEXT, empty_reason='no-progress-control')

    def advance_checkout_once(self) -> Dict[str, Any]:
        """Advance only reversible checkout-navigation controls; never final purchase."""
        return self._advance_once(_CHECKOUT_TEXT, empty_reason='no-checkout-control')

    def _advance_once(self, values: Iterable[str], *, empty_reason: str) -> Dict[str, Any]:
        cdp = getattr(self._sb, 'cdp', self._sb)
        if cdp is None:
            return {'advanced': False, 'reason': 'cdp-unavailable'}
        candidates = tuple(str(value) for value in values)
        if not self._candidate_exists(candidates):
            return {'advanced': False, 'reason': empty_reason}

        for root, scope in self._roots(cdp):
            for selector in _BUTTON_SELECTORS:
                for element in self._elements(root, selector):
                    text = self._element_text(element)
                    if not text or not self._matches(text, candidates):
                        continue
                    click = getattr(element, 'mouse_click', None)
                    if not callable(click):
                        continue
                    try:
                        click()
                        return {
                            'advanced': True,
                            'text': text,
                            'selector': selector,
                            'scope': scope,
                            'mode': 'cdp-mouse-click',
                        }
                    except Exception:
                        continue
        return {'advanced': False, 'reason': empty_reason}

    def _candidate_exists(self, values: Iterable[str]) -> bool:
        """Cheap read-only gate before SeleniumBase element queries with implicit waits.

        Runtime polling must stay cheap when no popup/progress control exists. The
        preflight recursively inspects only script-accessible documents; any real
        candidate still uses the existing trusted CDP mouse-click path below.
        """
        candidates = [str(value).strip().lower() for value in values if str(value).strip()]
        if not candidates:
            return False
        script = f"""
        (() => {{
          const candidates = {json.dumps(candidates)};
          const selectors = {json.dumps(list(_BUTTON_SELECTORS))};
          const normalize = value => String(value || '').trim().toLowerCase().replace(/\\s+/g, ' ');
          const matches = raw => {{
            const text = normalize(raw);
            if (!text) return false;
            return candidates.some(rawCandidate => {{
              const candidate = normalize(rawCandidate);
              if (!candidate) return false;
              if (text === candidate) return true;
              return candidate.includes(' ') && candidate.length > 8 && text.includes(candidate);
            }});
          }};
          const elementText = element =>
            element?.innerText || element?.textContent || element?.getAttribute?.('value') ||
            element?.getAttribute?.('aria-label') || element?.getAttribute?.('title') || '';
          const seen = new Set();
          const scan = doc => {{
            if (!doc || seen.has(doc)) return false;
            seen.add(doc);
            for (const selector of selectors) {{
              let elements = [];
              try {{ elements = Array.from(doc.querySelectorAll(selector)); }} catch (_) {{}}
              if (elements.some(element => matches(elementText(element)))) return true;
            }}
            let frames = [];
            try {{ frames = Array.from(doc.querySelectorAll('iframe,frame')); }} catch (_) {{}}
            for (const frame of frames) {{
              try {{ if (frame.contentDocument && scan(frame.contentDocument)) return true; }} catch (_) {{}}
            }}
            return false;
          }};
          return scan(document);
        }})()
        """
        try:
            evaluator = getattr(self._sb, 'evaluate', None)
            if callable(evaluator):
                return bool(evaluator(script))
            executor = getattr(self._sb, 'execute_script', None)
            if callable(executor):
                return bool(executor(f"return {script};"))
        except Exception:
            # Fail open to the established CDP query path when cheap observation
            # itself is unavailable; behavior is preserved rather than skipped.
            return True
        return True

    def _click_in_root(self, root: Any, *, fallback: bool = False) -> Dict[str, Any]:
        strong = (
            'alles akzeptieren', 'alle akzeptieren', 'alles zulassen', 'alle zulassen',
            'accept all', 'allow all', 'zustimmen', 'i agree', 'verstanden', 'einverstanden',
            'alle cookies akzeptieren', 'cookies akzeptieren', 'alle cookies erlauben', 'cookies erlauben',
        )
        for selector in _BUTTON_SELECTORS:
            elements = self._elements(root, selector)
            for element in elements:
                text = self._element_text(element)
                # Phrase-based match only; loose keyword matching clicked random
                # buttons and falsely reported success (tonies/Usercentrics).
                if not text or not self._matches(text, _ACCEPT_TEXT):
                    continue
                if fallback and not self._matches(text, strong):
                    continue
                click = getattr(element, 'mouse_click', None)
                if not callable(click):
                    continue
                try:
                    click()
                    return {
                        'dismissed': True,
                        'text': text,
                        'selector': selector,
                        'mode': 'cdp-mouse-click',
                    }
                except Exception:
                    continue
        return {'dismissed': False}

    _ACCEPT_KEYWORDS = (
        'akzeptieren', 'accept', 'allow', 'erlauben', 'zustimmen', 'einverstanden',
        'verstanden', 'stimme zu', 'i agree', 'alle cookies',
    )
    _REJECT_KEYWORDS = ('nur notwendige', 'nur essentielle', 'ablehnen', 'reject', 'nicht erlauben', 'nur erforderliche', 'notwendige cookies')

    def _accepts(self, text: str) -> bool:
        """Accept a consent button by exact/substring phrase OR by accept keyword."""
        if self._matches(text, _ACCEPT_TEXT):
            return True
        normalized = ' '.join(str(text or '').strip().lower().split())
        if not normalized:
            return False
        if any(reject in normalized for reject in self._REJECT_KEYWORDS):
            return False
        return any(keyword in normalized for keyword in self._ACCEPT_KEYWORDS)

    @classmethod
    def _roots(cls, cdp: Any):
        yield cdp, 'document'
        try:
            frames = list(cdp.find_elements('iframe') or [])
        except Exception:
            frames = []
        for frame in frames:
            yield frame, 'iframe'

    @staticmethod
    def _matches(text: str, values: Iterable[str]) -> bool:
        normalized = ' '.join(str(text or '').strip().lower().split())
        for value in values:
            candidate = ' '.join(str(value or '').strip().lower().split())
            if not candidate:
                continue
            if normalized == candidate:
                return True
            # Single-word controls are intentionally exact-only. This prevents
            # generic "continue"/"weiter" text from matching an irreversible
            # order control that merely contains the same word.
            if ' ' in candidate and len(candidate) > 8 and candidate in normalized:
                return True
        return False

    @staticmethod
    def _elements(root: Any, selector: str) -> Iterable[Any]:
        for name in ('find_elements', 'query_selector_all'):
            method = getattr(root, name, None)
            if not callable(method):
                continue
            try:
                return list(method(selector) or [])
            except Exception:
                continue
        return []

    @staticmethod
    def _element_text(element: Any) -> str:
        for name in ('text', 'text_content'):
            try:
                value = getattr(element, name, '')
                if callable(value):
                    value = value()
                text = ' '.join(str(value or '').strip().lower().split())
                if text:
                    return text
            except Exception:
                pass
        getter = getattr(element, 'get_attribute', None)
        if callable(getter):
            for attr in ('value', 'aria-label', 'title'):
                try:
                    text = ' '.join(str(getter(attr) or '').strip().lower().split())
                except Exception:
                    text = ''
                if text:
                    return text
        return ''
