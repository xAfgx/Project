from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

from stable_marks import build_stable_marks, stable_mark_digest


class SliderSiteAdapter:
    """Domain-agnostic structural detector for slider-style test interactions."""

    def __init__(self, seleniumbase_cdp: Any, *, overrides: Dict[str, str] | None = None) -> None:
        self._sb = seleniumbase_cdp
        self._overrides = self._clean_overrides(overrides or {})
        self._generation = 0
        self._last_signature = ""

    def poll(self) -> Dict[str, Any]:
        primary = self._snapshot_document()
        oopif = self._snapshot_oopif_frames()
        if bool(primary.get("complete")) or bool(primary.get("failed")):
            snapshot = primary
        elif bool(oopif.get("complete")) or bool(oopif.get("failed")):
            snapshot = oopif
        elif oopif.get("kind") == "slider" and (
            primary.get("kind") != "slider" or int(oopif.get("score") or 0) > int(primary.get("score") or 0)
        ):
            snapshot = oopif
        else:
            snapshot = primary
        return self._with_generation(snapshot)

    def _slider_expression(self) -> str:
        overrides = json.dumps(self._overrides)
        return f"""
        (() => {{
          const overrides = {overrides};
          const viewport = {{
            width: window.innerWidth || document.documentElement.clientWidth || 0,
            height: window.innerHeight || document.documentElement.clientHeight || 0,
            scrollX: window.scrollX || 0,
            scrollY: window.scrollY || 0,
            devicePixelRatio: window.devicePixelRatio || 1,
          }};
          const visible = el => {{
            if (!el?.getBoundingClientRect) return false;
            const r = el.getBoundingClientRect(), s = getComputedStyle(el);
            return r.width >= 10 && r.height >= 8 && s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
          }};
          const text = el => (el?.innerText || el?.textContent || el?.getAttribute?.('aria-label') || '').trim().replace(/\\s+/g, ' ');
          const rectOf = (el, offset={{x:0,y:0}}) => {{
            const r = el.getBoundingClientRect();
            return {{x:r.x + (offset?.x || 0),y:r.y + (offset?.y || 0),width:r.width,height:r.height}};
          }};
          const selectorFor = el => {{
            if (!el || el.getRootNode?.() !== document) return '';
            if (el.id) return '#' + CSS.escape(el.id);
            const testId = el.getAttribute?.('data-testid');
            if (testId) return '[data-testid="' + CSS.escape(testId) + '"]';
            if (el.matches?.('input[type="range"]')) {{
              const name = el.getAttribute('name');
              return name ? 'input[type="range"][name="' + CSS.escape(name) + '"]' : 'input[type="range"]';
            }}
            return '';
          }};
          const scopeToken = (el, fallbackIndex=0) => {{
            if (!el) return `slot:${{fallbackIndex}}`;
            const id = el.getAttribute?.('id') || '';
            const testId = el.getAttribute?.('data-testid') || '';
            const name = el.getAttribute?.('name') || '';
            const aria = el.getAttribute?.('aria-label') || '';
            const cls = typeof el.className === 'string' ? el.className.trim().split(/\\s+/).slice(0,3).join('.') : '';
            const tag = (el.tagName || 'node').toLowerCase();
            return [tag,id,testId,name,aria,cls,`slot:${{fallbackIndex}}`].join('|');
          }};
          const structuralKey = (el, role, fallbackIndex=0) => {{
            if (!el) return `${{role}}:slot:${{fallbackIndex}}`;
            const selector = selectorFor(el);
            if (selector) return selector;
            const id = el.getAttribute?.('id') || '';
            const testId = el.getAttribute?.('data-testid') || '';
            const name = el.getAttribute?.('name') || '';
            const aria = el.getAttribute?.('aria-label') || '';
            const roleAttr = el.getAttribute?.('role') || '';
            const cls = typeof el.className === 'string' ? el.className.trim().split(/\\s+/).slice(0,4).join('.') : '';
            return [role, el.tagName || '', id, testId, name, aria, roleAttr, cls, `slot:${{fallbackIndex}}`].join('|');
          }};
          const semanticSignature = (el, role) => {{
            if (!el) return role;
            const style = getComputedStyle(el);
            return [
              role,
              text(el).slice(0,240),
              el.getAttribute?.('aria-valuenow') || '',
              el.getAttribute?.('aria-valuetext') || '',
              el.getAttribute?.('data-target') || '',
              el.getAttribute?.('data-goal') || '',
              style.backgroundColor || '',
              style.borderColor || '',
            ].join('|');
          }};
          const numberValue = (...values) => {{
            for (const raw of values) {{
              if (raw === null || raw === undefined || String(raw).trim() === '') continue;
              const parsed = Number(raw);
              if (Number.isFinite(parsed)) return parsed;
            }}
            return null;
          }};
          const roots = [], seen = new Set();
          const walk = (root, scope, offset={{x:0,y:0}}) => {{
            if (!root || seen.has(root)) return;
            seen.add(root); roots.push([root, scope, offset]);
            const all = [...(root.querySelectorAll?.('*') || [])];
            for (const [index,el] of all.entries()) {{
              if (el.shadowRoot) walk(el.shadowRoot, scope + '/shadow:' + scopeToken(el,index), offset);
            }}
            const frames = [...(root.querySelectorAll?.('iframe') || [])];
            for (const [index,frame] of frames.entries()) {{
              try {{
                if (frame.contentDocument) {{
                  const r = frame.getBoundingClientRect();
                  walk(
                    frame.contentDocument,
                    scope + '/iframe:' + scopeToken(frame,index),
                    {{x:(offset?.x || 0) + r.left + Number(frame.clientLeft || 0), y:(offset?.y || 0) + r.top + Number(frame.clientTop || 0)}}
                  );
                }}
              }} catch (_) {{}}
            }}
          }};
          const center = r => [r.x + r.width/2, r.y + r.height/2];
          const distanceToRect = (x,y,r) => {{
            const dx = Math.max(r.x-x, 0, x-(r.x+r.width));
            const dy = Math.max(r.y-y, 0, y-(r.y+r.height));
            return Math.hypot(dx,dy);
          }};
          walk(document, 'document');

          const candidates = [];
          let explicitComplete = false;
          let explicitFailed = false;
          for (const [root, scope, offset] of roots) {{
            const scopedRoot = overrides.sliderRoot ? root.querySelector(overrides.sliderRoot) || root : root;
            let handles = [];
            if (overrides.sliderHandle) handles = [...scopedRoot.querySelectorAll(overrides.sliderHandle)].filter(visible);
            if (!handles.length) handles = [...scopedRoot.querySelectorAll('input[type="range"],[role="slider"],[aria-valuenow]')].filter(visible);
            if (!handles.length) {{
              const containers = ['slider','slide','track','drag','captcha','verify','unlock','puzzle'];
              const parts = ['handle','thumb','knob','btn','button','move','bar','item','drag'];
              const selectors = [];
              for (const c of containers) for (const p of parts) selectors.push('[class*="' + c + '" i] [class*="' + p + '" i]');
              for (const c of containers) selectors.push('[class*="' + c + '" i] > [class*="handle" i]','[class*="' + c + '" i] > [class*="btn" i]');
              selectors.push('.sliderContainer > .slider', '.sliderContainer .slider', '[class*="sliderContainer" i] .slider', '[class*="sliderMask" i] .slider', '[class*="slidercaptcha" i] .slider');
              handles = [...scopedRoot.querySelectorAll(selectors.join(','))].filter(visible);
            }}
            if (!handles.length) handles = [...scopedRoot.querySelectorAll('[class*="handle" i],[class*="thumb" i],[class*="knob" i]')].filter(visible);

            const complete = Boolean(overrides.sliderComplete && scopedRoot.querySelector(overrides.sliderComplete));
            const failed = Boolean(overrides.sliderFailed && scopedRoot.querySelector(overrides.sliderFailed));
            explicitComplete = explicitComplete || complete;
            explicitFailed = explicitFailed || failed;

            for (const handle of handles) {{
              const nativeRange = handle.matches('input[type="range"]');
              let track = overrides.sliderTrack ? scopedRoot.querySelector(overrides.sliderTrack) : null;
              if (!track && nativeRange) track = handle;
              if (!track) {{
                const sliderContainer = handle.closest('.sliderContainer, [class*="sliderContainer" i]');
                if (sliderContainer) track = sliderContainer.querySelector('.sliderbg, [class*="sliderbg" i], [class*="track" i]') || sliderContainer;
              }}
              if (!track && handle.matches?.('.slider') && handle.parentElement?.matches?.('.sliderContainer')) track = handle.parentElement.querySelector('.sliderbg');
              if (!track) track = handle.parentElement?.closest('[role="slider"],[class*="slider" i],[class*="slide" i],[class*="track" i],[class*="drag" i],[class*="captcha" i],[class*="verify" i],[class*="unlock" i],[class*="puzzle" i]') || handle.parentElement;
              if (!track || !visible(track)) continue;

              const h = handle.getBoundingClientRect(), t = track.getBoundingClientRect();
              const horizontal = t.width >= t.height;
              const min = nativeRange
                ? (numberValue(handle.min, handle.getAttribute('min'), 0) ?? 0)
                : (numberValue(handle.getAttribute('aria-valuemin'), handle.min, 0) ?? 0);
              const max = nativeRange
                ? (numberValue(handle.max, handle.getAttribute('max'), 100) ?? 100)
                : (numberValue(handle.getAttribute('aria-valuemax'), handle.max, 100) ?? 100);
              const value = nativeRange
                ? (numberValue(handle.value, handle.getAttribute('value'), min) ?? min)
                : (numberValue(handle.getAttribute('aria-valuenow'), handle.value, min) ?? min);
              const span = Math.max(1, max - min);
              const fraction = Math.max(0, Math.min(1, (value - min) / span));
              const instruction = overrides.sliderInstruction
                ? scopedRoot.querySelector(overrides.sliderInstruction)
                : handle.closest('.sliderContainer')?.previousElementSibling?.matches?.('.sliderText')
                  ? handle.closest('.sliderContainer').previousElementSibling
                  : track.parentElement?.previousElementSibling || track.parentElement;

              const targetSelector = overrides.sliderTarget || '.sliderContainer .sliderTarget,[data-target],[data-goal],[aria-label*="target" i],[aria-label*="goal" i],[class*="target" i],[class*="goal" i],[class*="marker" i],[class*="tick" i]';
              const targetNodes = [...(scopedRoot.querySelectorAll?.(targetSelector) || [])]
                .filter(el => el !== handle && el !== track && visible(el));
              const rawTargets = [];
              for (const [targetIndex,node] of targetNodes.entries()) {{
                const r = node.getBoundingClientRect();
                const [cx,cy] = center(r);
                const distance = distanceToRect(cx,cy,t);
                const nearLimit = Math.max(36, horizontal ? t.height*4 : t.width*4);
                if (distance > nearLimit) continue;
                const targetFraction = horizontal
                  ? (cx - t.left) / Math.max(1,t.width)
                  : (t.bottom - cy) / Math.max(1,t.height);
                if (targetFraction < -0.12 || targetFraction > 1.12) continue;
                const label = text(node);
                const identity = `${{node.id || ''}} ${{node.className || ''}} ${{node.getAttribute?.('aria-label') || ''}}`;
                let targetScore = 50;
                if (/target|goal|ziel|marker|tick/i.test(identity)) targetScore += 20;
                if (overrides.sliderTarget) targetScore += 25;
                if (label) targetScore += 5;
                if (distance <= 4) targetScore += 10;
                rawTargets.push({{
                  role:'slider-target',
                  fraction:Math.max(0,Math.min(1,targetFraction)),
                  score:targetScore,
                  confidence:Math.max(0.5,Math.min(0.96,0.45 + targetScore/180)),
                  label:label.slice(0,160),
                  visualBounds:rectOf(node, offset),
                  selector:selectorFor(node),
                  structuralKey:structuralKey(node,'slider-target',targetIndex),
                  semanticSignature:semanticSignature(node,'slider-target'),
                }});
              }}
              rawTargets.sort((a,b) => b.score-a.score);

              const rawMarks = [
                {{
                  role:'slider-handle', visualBounds:rectOf(handle, offset), confidence:0.98,
                  selector:overrides.sliderHandle || selectorFor(handle),
                  structuralKey:structuralKey(handle,'slider-handle',0),
                  semanticSignature:semanticSignature(handle,'slider-handle'),
                }},
                {{
                  role:'slider-track', visualBounds:rectOf(track, offset), confidence:0.96,
                  selector:overrides.sliderTrack || selectorFor(track),
                  structuralKey:structuralKey(track,'slider-track',0),
                  semanticSignature:semanticSignature(track,'slider-track'),
                }},
                ...rawTargets,
              ];

              let score = 45;
              if (handle.matches('input[type="range"],[role="slider"]')) score += 25;
              if (overrides.sliderHandle || overrides.sliderTrack) score += 20;
              if (handle.matches?.('.slider')) score += 20;
              if (t.width >= 120 || t.height >= 120) score += 10;
              if (text(instruction)) score += 5;
              if (rawTargets.length) score += 8;
              candidates.push({{
                kind:'slider', scope, score,
                orientation: horizontal ? 'horizontal' : 'vertical',
                fraction, min, max, value,
                instruction:text(instruction).slice(0,600),
                handleRect:rectOf(handle, offset),
                trackRect:rectOf(track, offset),
                handleSelector: overrides.sliderHandle || selectorFor(handle),
                trackSelector: overrides.sliderTrack || selectorFor(track),
                nativeRange,
                rawMarks,
                viewport,
                complete,
                failed,
                override:Boolean(overrides.sliderRoot || overrides.sliderHandle || overrides.sliderTrack || overrides.sliderTarget)
              }});
            }}
          }}
          candidates.sort((a,b) => b.score-a.score);
          return candidates[0] || {{kind:'none',scope:'document',score:0,orientation:'horizontal',fraction:0,min:0,max:0,value:0,instruction:'',handleRect:null,trackRect:null,handleSelector:'',trackSelector:'',nativeRange:false,rawMarks:[],viewport,complete:explicitComplete,failed:explicitFailed,override:false}};
        }})()
        """

    def _snapshot_document(self) -> Dict[str, Any]:
        try:
            value = self._evaluate(self._slider_expression())
        except Exception:
            return self._empty()
        return self._normalize(value)

    def _snapshot_oopif_frames(self) -> Dict[str, Any]:
        discover = getattr(self._sb, "ares_oopif_discover", None)
        evaluate = getattr(self._sb, "ares_oopif_evaluate", None)
        if not callable(discover) or not callable(evaluate):
            return self._empty("oopif")
        try:
            frames = list(discover() or [])
        except Exception:
            return self._empty("oopif")

        best = self._empty("oopif")
        outcome: Dict[str, Any] | None = None
        expression = self._slider_expression()
        for entry in frames:
            if not isinstance(entry, dict):
                continue
            path = [str(value) for value in entry.get("path") or [] if str(value)]
            if not path:
                continue
            try:
                evaluated = evaluate(path, f"return {expression.strip()};", [])
            except Exception:
                continue
            if not isinstance(evaluated, dict):
                continue
            value = evaluated.get("value")
            if not isinstance(value, dict):
                continue
            scope_suffix = str(value.get("scope") or "document")
            scope = "oopif:" + "/".join(path)
            if scope_suffix and scope_suffix != "document":
                scope += "/" + scope_suffix
            if value.get("kind") != "slider":
                if bool(value.get("complete")) or bool(value.get("failed")):
                    outcome = {
                        **self._empty(scope),
                        "complete": bool(value.get("complete")),
                        "failed": bool(value.get("failed")),
                        "framePath": path,
                        "documentEpoch": int(evaluated.get("documentEpoch") or 0),
                        "sessionGeneration": int(evaluated.get("sessionGeneration") or 0),
                    }
                continue
            try:
                offset_x = float(evaluated.get("offsetX") or 0.0)
                offset_y = float(evaluated.get("offsetY") or 0.0)
            except (TypeError, ValueError):
                offset_x = offset_y = 0.0

            adjusted = dict(value)
            adjusted["scope"] = scope
            adjusted["framePath"] = path
            adjusted["documentEpoch"] = int(evaluated.get("documentEpoch") or 0)
            adjusted["sessionGeneration"] = int(evaluated.get("sessionGeneration") or 0)
            for key in ("handleRect", "trackRect"):
                rect = adjusted.get(key)
                if isinstance(rect, dict):
                    item = dict(rect)
                    item["x"] = float(item.get("x") or 0.0) + offset_x
                    item["y"] = float(item.get("y") or 0.0) + offset_y
                    adjusted[key] = item
            raw_marks = []
            for raw in adjusted.get("rawMarks") or []:
                if not isinstance(raw, dict):
                    continue
                item = dict(raw)
                bounds = item.get("visualBounds")
                if isinstance(bounds, dict):
                    shifted = dict(bounds)
                    shifted["x"] = float(shifted.get("x") or 0.0) + offset_x
                    shifted["y"] = float(shifted.get("y") or 0.0) + offset_y
                    item["visualBounds"] = shifted
                raw_marks.append(item)
            adjusted["rawMarks"] = raw_marks
            candidate = self._normalize(adjusted)
            if candidate.get("kind") == "slider" and int(candidate.get("score") or 0) > int(best.get("score") or 0):
                best = candidate
        return best if best.get("kind") == "slider" else (outcome if outcome is not None else best)

    def _with_generation(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        target_state = [
            [
                str(item.get("markId") or ""),
                round(float(item.get("fraction") or 0.0), 4),
                str(item.get("semanticVisualSignature") or ""),
            ]
            for item in snapshot.get("targetCandidates") or []
            if isinstance(item, dict)
        ]
        signature_input = "|".join([
            str(snapshot.get("kind") or "none"),
            str(snapshot.get("scope") or ""),
            str(snapshot.get("documentEpoch") or 0),
            str(snapshot.get("sessionGeneration") or 0),
            str(snapshot.get("orientation") or ""),
            str(round(float(snapshot.get("fraction") or 0), 4)),
            str(snapshot.get("instruction") or ""),
            stable_mark_digest(snapshot.get("marks") or []),
            json.dumps(target_state, sort_keys=True),
            str(bool(snapshot.get("complete"))),
            str(bool(snapshot.get("failed"))),
        ])
        signature = hashlib.sha256(signature_input.encode("utf-8", errors="ignore")).hexdigest()
        if signature != self._last_signature:
            self._generation += 1
            self._last_signature = signature
        return {**snapshot, "generation": self._generation, "signature": signature}

    def _evaluate(self, script: str) -> Any:
        evaluator = getattr(self._sb, "evaluate", None)
        if callable(evaluator):
            return evaluator(script)
        return self._sb.execute_script(f"return {script};")

    @staticmethod
    def _clean_overrides(values: Dict[str, str]) -> Dict[str, str]:
        allowed = {"sliderRoot", "sliderHandle", "sliderTrack", "sliderTarget", "sliderInstruction", "sliderComplete", "sliderFailed"}
        return {key: str(value).strip() for key, value in values.items() if key in allowed and str(value).strip()}

    @staticmethod
    def _normalize(value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return SliderSiteAdapter._empty()
        scope = str(value.get("scope") or "document")
        marks = build_stable_marks(
            [dict(item) for item in value.get("rawMarks") or [] if isinstance(item, dict)],
            scope=scope,
            viewport=value.get("viewport") if isinstance(value.get("viewport"), dict) else {},
        )
        targets = [
            {
                "markId": mark.get("markId"),
                "fraction": mark.get("fraction"),
                "score": mark.get("score"),
                "confidence": mark.get("confidence"),
                "label": mark.get("label", ""),
                "rect": mark.get("visualBounds"),
                "semanticVisualSignature": mark.get("semanticVisualSignature"),
            }
            for mark in marks
            if mark.get("role") == "slider-target"
        ]
        return {
            "kind": str(value.get("kind") or "none"),
            "scope": scope,
            "score": int(value.get("score") or 0),
            "orientation": str(value.get("orientation") or "horizontal"),
            "fraction": float(value.get("fraction") or 0),
            "min": float(value.get("min") or 0),
            "max": float(value.get("max") or 0),
            "value": float(value.get("value") or 0),
            "instruction": str(value.get("instruction") or ""),
            "handleRect": value.get("handleRect"),
            "trackRect": value.get("trackRect"),
            "handleSelector": str(value.get("handleSelector") or ""),
            "trackSelector": str(value.get("trackSelector") or ""),
            "nativeRange": bool(value.get("nativeRange")),
            "targetCandidates": targets,
            "marks": marks,
            "complete": bool(value.get("complete")),
            "failed": bool(value.get("failed")),
            "override": bool(value.get("override")),
            "framePath": [str(item) for item in value.get("framePath") or [] if str(item)],
            "documentEpoch": int(value.get("documentEpoch") or 0),
            "sessionGeneration": int(value.get("sessionGeneration") or 0),
        }

    @staticmethod
    def _empty(scope: str = "document") -> Dict[str, Any]:
        return {"kind":"none","scope":scope,"score":0,"orientation":"horizontal","fraction":0.0,"min":0.0,"max":0.0,"value":0.0,"instruction":"","handleRect":None,"trackRect":None,"handleSelector":"","trackSelector":"","nativeRange":False,"targetCandidates":[],"marks":[],"complete":False,"failed":False,"override":False,"framePath":[],"documentEpoch":0,"sessionGeneration":0}
