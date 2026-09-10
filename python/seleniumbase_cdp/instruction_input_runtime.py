from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List


INPUT_SELECTOR = (
    'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):'
    'not([type="checkbox"]):not([type="radio"]):not([type="password"]), textarea, select'
)


class InstructionInputRuntime:
    """Detect simple page instructions that ask for text/number entry and execute them safely."""

    def __init__(self, sb: Any) -> None:
        self._sb = sb

    def observe(self) -> Dict[str, Any]:
        script = r"""
const selector = arguments[0];
const controls = Array.from(document.querySelectorAll(selector)).filter(el => {
  const r = el.getBoundingClientRect();
  const s = getComputedStyle(el);
  return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && s.display !== 'none';
});
const text = v => String(v || '').replace(/\s+/g, ' ').trim();
const pageText = text(document.body?.innerText || '').slice(0, 5000);
return {
  pageText,
  controls: controls.map((el, index) => {
    const id = `ares-instruction-${index}`;
    el.setAttribute('data-ares-instruction-id', id);
    return {
      fieldId: id,
      index,
      tagName: text(el.tagName).toLowerCase(),
      inputType: text(el.getAttribute('type')).toLowerCase(),
      placeholder: text(el.getAttribute('placeholder')),
      ariaLabel: text(el.getAttribute('aria-label')),
      value: text(el.value)
    };
  })
};
"""
        result = self._sb.execute_script(script, INPUT_SELECTOR)
        return dict(result) if isinstance(result, dict) else {"pageText": "", "controls": []}

    @staticmethod
    def _format_decimal(value: Decimal) -> str:
        if not value.is_finite():
            return ""
        if value == value.to_integral_value():
            return str(int(value))
        normalized = format(value.normalize(), "f")
        return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized

    @classmethod
    def _infer_arithmetic(cls, page_text: str) -> str:
        match = re.search(
            r"\b(?:was\s+ist|berechne|rechne|calculate|compute|what\s+is)\s*"
            r"([-+]?\d+(?:[.,]\d+)?)\s*([+\-*/])\s*([-+]?\d+(?:[.,]\d+)?)"
            r"\s*(?:[?=.!]|$)",
            page_text,
            flags=re.IGNORECASE,
        )
        if not match:
            return ""
        try:
            left = Decimal(match.group(1).replace(",", "."))
            right = Decimal(match.group(3).replace(",", "."))
            operator = match.group(2)
            if operator == "+":
                result = left + right
            elif operator == "-":
                result = left - right
            elif operator == "*":
                result = left * right
            else:
                if right == 0:
                    return ""
                result = left / right
        except (InvalidOperation, ArithmeticError):
            return ""
        return cls._format_decimal(result)

    def infer(self) -> Dict[str, Any]:
        observation = self.observe()
        controls: List[Dict[str, Any]] = [dict(item) for item in observation.get("controls", []) if isinstance(item, dict)]
        if not controls:
            return {"matched": False, "reason": "no-input-control"}

        page_text = str(observation.get("pageText") or "")
        value = self._infer_arithmetic(page_text)
        patterns = [
            r"\b(?:gib|trage)\s+[\"'„“]?([A-Za-z0-9][A-Za-z0-9 _.,:+\-/]{0,80}?)[\"'„“]?\s+ein\b",
            r"\b(?:enter|type|input|write|tippe)\s+(?:in\s+)?(?:the\s+)?(?:text|value|number|zahl|wert)?\s*[:\-]?\s*[\"'„“]?([A-Za-z0-9][A-Za-z0-9 _.,:+\-/]{0,80}?)[\"'„“]?(?=\s*(?:[.!?]|$))",
            r"\b(?:value|wert|number|zahl)\s*[:=]\s*[\"'„“]?([A-Za-z0-9][A-Za-z0-9 _.,:+\-/]{0,80}?)[\"'„“]?(?=\s*(?:[.!?]|$))",
        ]
        if not value:
            for pattern in patterns:
                match = re.search(pattern, page_text, flags=re.IGNORECASE)
                if match:
                    value = match.group(1).strip().rstrip(".")
                    break
        if not value:
            return {"matched": False, "reason": "no-simple-instruction"}

        empty = [item for item in controls if not str(item.get("value") or "").strip()]
        numeric = re.fullmatch(r"[-+]?\d+(?:[.,]\d+)?", value) is not None
        candidates = empty or controls
        if numeric:
            number_fields = [item for item in candidates if str(item.get("inputType") or "").lower() in {"number", "range"}]
            if number_fields:
                candidates = number_fields
        target = candidates[0]
        if str(target.get("value") or "") == value:
            return {"matched": False, "reason": "already-satisfied", "fieldId": str(target.get("fieldId") or ""), "value": value}
        return {"matched": True, "fieldId": str(target.get("fieldId") or ""), "value": value}

    def apply(self) -> Dict[str, Any]:
        decision = self.infer()
        if not decision.get("matched"):
            return {"acted": False, "verified": decision.get("reason") == "already-satisfied", "kind": "instruction-input", **decision}

        script = r"""
const fieldId = arguments[0];
const wanted = String(arguments[1] ?? '');
const el = document.querySelector(`[data-ares-instruction-id="${CSS.escape(fieldId)}"]`);
if (!el) return { acted: false, verified: false, reason: 'field-not-found', observedValue: '' };
el.scrollIntoView({block:'center', inline:'nearest'});
el.focus({preventScroll:true});
if (el instanceof HTMLSelectElement) {
  const lower = wanted.toLocaleLowerCase();
  const option = Array.from(el.options).find(o => String(o.value).toLocaleLowerCase() === lower)
    || Array.from(el.options).find(o => String(o.textContent || '').trim().toLocaleLowerCase() === lower);
  if (!option) return { acted: false, verified: false, reason: 'select-option-not-found', observedValue: String(el.value || '') };
  el.value = option.value;
} else {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
  if (setter) setter.call(el, wanted); else el.value = wanted;
}
el.dispatchEvent(new Event('input', {bubbles:true}));
el.dispatchEvent(new Event('change', {bubbles:true}));
const observedValue = String(el.value ?? '');
const verified = observedValue === wanted || (el instanceof HTMLSelectElement && observedValue.length > 0);
return { acted: true, verified, reason: verified ? '' : 'value-mismatch', observedValue };
"""
        result = self._sb.execute_script(script, decision["fieldId"], decision["value"])
        payload = dict(result) if isinstance(result, dict) else {"acted": False, "verified": False, "reason": "invalid-result"}
        return {"kind": "instruction-input", "requestedValue": decision["value"], **payload}
