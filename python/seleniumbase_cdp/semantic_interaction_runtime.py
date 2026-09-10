from __future__ import annotations

from typing import Any, Dict, Iterable, List


CONTROL_SELECTOR = (
    'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):'
    'not([type="checkbox"]):not([type="radio"]):not([type="password"]), select, textarea'
)


class SemanticInteractionRuntime:
    """Portable semantic field observation/execution on the SeleniumBase-owned page."""

    def __init__(self, sb: Any) -> None:
        self._sb = sb

    def observe_fields(self) -> List[Dict[str, Any]]:
        script = r"""
const selector = arguments[0];
const controls = Array.from(document.querySelectorAll(selector));
const text = value => String(value || '').replace(/\s+/g, ' ').trim();
const labelFor = el => {
  const explicit = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
  const wrapping = el.closest('label');
  return text(explicit?.innerText || wrapping?.innerText || '');
};
const nearby = el => {
  const parent = el.parentElement;
  if (!parent) return '';
  return text(parent.innerText || '').slice(0, 260);
};
return controls.map((el, index) => {
  const fieldId = `ares-semantic-${index}`;
  el.setAttribute('data-ares-semantic-id', fieldId);
  return {
    fieldId,
    index,
    tagName: text(el.tagName).toLowerCase(),
    inputType: text(el.getAttribute('type')).toLowerCase(),
    name: text(el.getAttribute('name')),
    id: text(el.id),
    autocomplete: text(el.getAttribute('autocomplete')),
    placeholder: text(el.getAttribute('placeholder')),
    ariaLabel: text(el.getAttribute('aria-label')),
    label: labelFor(el),
    nearbyText: nearby(el)
  };
});
"""
        result = self._sb.execute_script(script, CONTROL_SELECTOR)
        return [dict(item) for item in result] if isinstance(result, list) else []

    def execute_plan(self, plan: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        items = [dict(item) for item in plan]
        results: List[Dict[str, Any]] = []
        for item in items:
            field_id = str(item.get("fieldId") or "").strip()
            value = str(item.get("value") or "")
            if not field_id:
                results.append(self._result(item, False, "missing-field-id", ""))
                continue
            result = self._execute_one(field_id, value)
            results.append(self._result(
                item,
                bool(result.get("verified")),
                str(result.get("reason") or ""),
                str(result.get("observedValue") or ""),
            ))

        applied = sum(1 for result in results if result["verified"])
        return {
            "planned": len(items),
            "applied": applied,
            "verified": applied == len(items),
            "results": results,
            "fallbackNeeded": [result for result in results if not result["verified"]],
        }

    def _execute_one(self, field_id: str, value: str) -> Dict[str, Any]:
        script = r"""
const fieldId = arguments[0];
const value = String(arguments[1] ?? '');
const el = document.querySelector(`[data-ares-semantic-id="${CSS.escape(fieldId)}"]`);
if (!el) return { verified: false, reason: 'field-not-found', observedValue: '' };
if (el.disabled) return { verified: false, reason: 'field-disabled', observedValue: String(el.value ?? '') };

el.scrollIntoView({ block: 'center', inline: 'nearest' });
el.focus({ preventScroll: true });

if (el instanceof HTMLSelectElement) {
  const options = Array.from(el.options);
  const wanted = value.toLocaleLowerCase();
  const match = options.find(option => String(option.value).toLocaleLowerCase() === wanted)
    || options.find(option => String(option.textContent || '').trim().toLocaleLowerCase() === wanted);
  if (!match) return { verified: false, reason: 'select-option-not-found', observedValue: String(el.value ?? '') };
  el.value = match.value;
} else {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
  if (descriptor?.set) descriptor.set.call(el, value);
  else el.value = value;
}

el.dispatchEvent(new Event('input', { bubbles: true }));
el.dispatchEvent(new Event('change', { bubbles: true }));
const observedValue = String(el.value ?? '');
return { verified: observedValue === value || (el instanceof HTMLSelectElement && observedValue.length > 0), reason: '', observedValue };
"""
        result = self._sb.execute_script(script, field_id, value)
        return dict(result) if isinstance(result, dict) else {
            "verified": False,
            "reason": "invalid-executor-result",
            "observedValue": "",
        }

    @staticmethod
    def _result(item: Dict[str, Any], verified: bool, reason: str, observed_value: str) -> Dict[str, Any]:
        return {
            "fieldId": str(item.get("fieldId") or ""),
            "intent": str(item.get("intent") or "unknown"),
            "context": str(item.get("context") or "unknown"),
            "confidence": float(item.get("confidence") or 0.0),
            "verified": verified,
            "reason": reason,
            "observedValue": observed_value,
        }
