from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

from stable_marks import build_stable_marks, stable_mark_digest


_GRID_SIZES = {9: (3, 3), 16: (4, 4)}
_GRID_SIZES.update({4: (2, 2), 6: (2, 3), 8: (2, 4), 12: (3, 4), 20: (4, 5), 25: (5, 5), 36: (6, 6), 49: (7, 7), 64: (8, 8)})


class GridSiteAdapter:
    """Read-only, domain-agnostic structural adapter for visual test grids."""

    def __init__(self, seleniumbase_cdp: Any, *, overrides: Dict[str, str] | None = None) -> None:
        self._sb = seleniumbase_cdp
        self._overrides = self._clean_overrides(overrides or {})
        self._generation = 0
        self._last_signature = ""

    @classmethod
    def from_json(cls, seleniumbase_cdp: Any, path: str | Path | None) -> "GridSiteAdapter":
        if not path:
            return cls(seleniumbase_cdp)
        raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Site adapter override file must contain a JSON object.")
        return cls(seleniumbase_cdp, overrides={str(k): str(v) for k, v in raw.items()})

    def poll(self) -> Dict[str, Any]:
        snapshot = self._snapshot_document()
        if snapshot.get("kind") == "none" and not snapshot.get("complete") and not snapshot.get("failed"):
            snapshot = self._snapshot_nested_frames()
        return self._with_generation(snapshot)

    def _snapshot_document(self) -> Dict[str, Any]:
        overrides = json.dumps(self._overrides)
        grid_sizes = json.dumps({str(k): list(v) for k, v in _GRID_SIZES.items()})
        script = f"""
        (() => {{
          const overrides = {overrides};
          const GRID = new Map(Object.entries({grid_sizes}).map(([k,v]) => [Number(k), v]));
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
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
          }};
          const visualReady = el => {{
            if (!el) return false;
            if (el instanceof HTMLImageElement) return Boolean(el.complete && el.naturalWidth > 0 && el.naturalHeight > 0);
            if (el instanceof HTMLCanvasElement) return el.width > 0 && el.height > 0;
            return true;
          }};
          const text = el => (el?.innerText || el?.textContent || el?.getAttribute?.('aria-label') || '').trim().replace(/\\s+/g, ' ');
          const rectOf = (el, offset) => {{
            const r = el.getBoundingClientRect();
            return {{x:r.x + (offset?.x || 0), y:r.y + (offset?.y || 0), width:r.width, height:r.height}};
          }};
          const selectorFor = el => {{
            if (!el || el.getRootNode?.() !== document) return '';
            if (el.id) return '#' + CSS.escape(el.id);
            const testId = el.getAttribute?.('data-testid');
            if (testId) return '[data-testid="' + CSS.escape(testId) + '"]';
            const name = el.getAttribute?.('name');
            if (name) return `${{el.tagName.toLowerCase()}}[name="${{CSS.escape(name)}}"]`;
            return '';
          }};
          const structuralKey = (el, index) => {{
            const selector = selectorFor(el);
            if (selector) return selector;
            const id = el?.getAttribute?.('id') || '';
            const testId = el?.getAttribute?.('data-testid') || '';
            const aria = el?.getAttribute?.('aria-label') || '';
            const role = el?.getAttribute?.('role') || '';
            return ['grid-tile',el?.tagName||'',id,testId,aria,role,`slot:${{index}}`].join('|');
          }};
          const bgUrl = el => {{
            if (!el || !visible(el)) return '';
            const bg = getComputedStyle(el).backgroundImage || '';
            const match = bg.match(/url\\(["']?(.*?)["']?\\)/i);
            return match?.[1] || '';
          }};
          const canvasSource = canvas => {{
            try {{ return (window.__aresRawCanvas?.toDataURL ? window.__aresRawCanvas.toDataURL(canvas, 'image/png') : canvas.toDataURL('image/png')) || ''; }} catch (_) {{ return ''; }}
          }};
          const sourceOf = tile => {{
            if (!tile) return '';
            const img = tile.matches?.('img') ? tile : tile.querySelector?.('img');
            if (img) return img.currentSrc || img.src || img.getAttribute('src') || img.getAttribute('data-src') || '';
            const canvas = tile.matches?.('canvas') ? tile : tile.querySelector?.('canvas');
            if (canvas) return canvasSource(canvas);
            return bgUrl(tile);
          }};
          const semanticSignature = (tile, source) => {{
            const img = tile?.matches?.('img') ? tile : tile?.querySelector?.('img');
            const alt = img?.getAttribute?.('alt') || tile?.getAttribute?.('aria-label') || text(tile).slice(0,180);
            return ['grid-tile',alt,source].join('|');
          }};
          const tileFor = visual => {{
            if (visual?.matches?.('img') && /rc-image-tile|rc-imageselect/i.test(String(visual.className || ''))) {{
              const cell = visual.closest?.('td.rc-imageselect-tile,[class*="rc-imageselect-tile" i]');
              if (cell) return cell;
            }}
            return visual.closest?.('button,[role="button"],[tabindex],label,li,[class*="tile" i],[class*="cell" i]') || visual;
          }};
          const isControl = el => /^recaptcha-/i.test(String(el?.id || el?.getAttribute?.('id') || ''));
          const visualsIn = root => {{
            const items = [...(root.querySelectorAll?.('img,canvas') || [])].filter(el => visible(el) && visualReady(el) && !isControl(el));
            const bgCandidates = [...(root.querySelectorAll?.('button,[role="button"],[tabindex],label,li,[class*="tile" i],[class*="cell" i],[class*="image" i]') || [])]
              .filter(el => visible(el) && bgUrl(el) && !isControl(el));
            return [...new Set([...items, ...bgCandidates])];
          }};
          const roots = [], seen = new Set();
          const walkRoot = (root, label, offset = {{x:0, y:0}}) => {{
            if (!root || seen.has(root)) return;
            seen.add(root); roots.push([root, label, offset]);
            for (const el of root.querySelectorAll?.('*') || []) if (el.shadowRoot) walkRoot(el.shadowRoot, label + '/shadow', offset);
            for (const frame of root.querySelectorAll?.('iframe') || []) {{
              try {{
                if (frame.contentDocument) {{
                  const r = frame.getBoundingClientRect();
                  walkRoot(frame.contentDocument, label + '/iframe', {{x:(offset?.x || 0) + r.x, y:(offset?.y || 0) + r.y}});
                }}
              }} catch (_) {{}}
            }}
          }};
          const instructionNear = (root, groupRoot) => {{
            if (overrides.instruction) return root.querySelector(overrides.instruction);
            if (groupRoot?.previousElementSibling) return groupRoot.previousElementSibling;
            const parent = groupRoot?.parentElement;
            if (!parent) return null;
            const choices = [...parent.querySelectorAll('h1,h2,h3,h4,p,[class*="instruction" i],[class*="prompt" i],[class*="question" i]')].filter(visible);
            return choices.find(el => el.compareDocumentPosition(groupRoot) & Node.DOCUMENT_POSITION_FOLLOWING) || choices[0] || null;
          }};
          walkRoot(document, 'document');

          let explicitComplete = false;
          let explicitFailed = false;
          const candidates = [];
          for (const [root, scope, offset] of roots) {{
            const completeEl = overrides.complete ? root.querySelector(overrides.complete) : null;
            const failedEl = overrides.failed ? root.querySelector(overrides.failed) : null;
            const complete = Boolean(completeEl && visible(completeEl));
            const failed = Boolean(failedEl && visible(failedEl));
            explicitComplete = explicitComplete || complete;
            explicitFailed = explicitFailed || failed;

            const groups = [];
            if (overrides.tiles) {{
              const tiles = [...root.querySelectorAll(overrides.tiles)].filter(el => visible(el) && (!el.matches?.('img,canvas') || visualReady(el)));
              if (GRID.has(tiles.length)) groups.push({{root: overrides.root ? root.querySelector(overrides.root) || root : root, tiles, override:true}});
            }}

            if (!groups.length) {{
              const parents = new Set();
              for (const visual of visualsIn(root)) {{
                let node = tileFor(visual);
                for (let depth=0; node && depth<5; depth++, node=node.parentElement) if (node.parentElement) parents.add(node.parentElement);
              }}
              for (const parent of parents) {{
                const tiles = [...new Set(visualsIn(parent).map(tileFor))].filter(visible);
                if (!GRID.has(tiles.length)) continue;
                groups.push({{root:parent, tiles, override:false}});
              }}
            }}

            for (const group of groups) {{
              const count = group.tiles.length;
              if (!GRID.has(count)) continue;
              const [rows, columns] = GRID.get(count);
              const rects = group.tiles.map(tile => tile.getBoundingClientRect());
              const avgW = rects.reduce((a,r) => a+r.width,0)/count;
              const avgH = rects.reduce((a,r) => a+r.height,0)/count;
              const avgSide = Math.min(avgW, avgH);
              const regular = rects.filter(r => Math.abs(r.width-avgW)<=Math.max(12,avgW*.35) && Math.abs(r.height-avgH)<=Math.max(12,avgH*.35)).length;
              const clickableFlags = group.tiles.map(tile => Boolean(tile.matches?.('button,[role="button"],[tabindex],label') || tile.onclick));
              const clickable = clickableFlags.filter(Boolean).length;
              const sources = group.tiles.map(sourceOf);
              const sourceCount = sources.filter(Boolean).length;
              const rawMarks = group.tiles.map((tile,index) => ({{
                role:'grid-tile',
                visualBounds:rectOf(tile, offset),
                confidence:Math.max(0.58,Math.min(0.98,0.68 + (sources[index] ? 0.18 : 0) + (clickableFlags[index] ? 0.10 : 0))),
                selector:selectorFor(tile),
                structuralKey:structuralKey(tile,index),
                semanticSignature:semanticSignature(tile,sources[index]),
                source:sources[index],
                label:text(tile).slice(0,160),
                score:index,
              }}));
              const instructionEl = instructionNear(root, group.root);
              const confirmRx = /(verify|submit|bestätig|bestaetig|weiter|continue|prüf|pruef|überprüfen|ueberpruefen|absenden|senden|confirm|check|done|next|ok|ja|yes)/i;
              const challengeRx = /(recaptcha|rc-imageselect|challenge|captcha)/i;
              const gridRect = group.tiles.reduce((acc, tile) => {{
                const r = tile.getBoundingClientRect();
                return {{left:Math.min(acc.left,r.left),top:Math.min(acc.top,r.top),right:Math.max(acc.right,r.right),bottom:Math.max(acc.bottom,r.bottom)}};
              }}, {{left:Infinity,top:Infinity,right:-Infinity,bottom:-Infinity}});
              let scopeRoot = group.root.parentElement || group.root;
              for (let node = group.tiles[0]; node && node !== document; node = node.parentElement) {{
                const cls = typeof node.className === 'string' ? node.className : '';
                if (challengeRx.test(String(node.id || '') + ' ' + cls)) {{ scopeRoot = node; break; }}
              }}
              const inPuzzleWindow = el => {{
                if (!el || !visible(el) || group.tiles.includes(el)) return false;
                if (scopeRoot.contains && el !== scopeRoot && !scopeRoot.contains(el)) return false;
                const r = el.getBoundingClientRect();
                const cx = r.left + r.width / 2;
                return r.top >= gridRect.top - 12 && r.top <= gridRect.bottom + 260
                  && cx >= gridRect.left - 80 && cx <= gridRect.right + 80;
              }};
              let submitEl = null;
              if (overrides.submit) {{
                const exact = root.querySelector(overrides.submit);
                if (exact && inPuzzleWindow(exact)) submitEl = exact;
              }}
              if (!submitEl) {{
                const verify = scopeRoot.querySelector?.('#recaptcha-verify-button, .rc-button-default');
                if (inPuzzleWindow(verify)) submitEl = verify;
              }}
              if (!submitEl) {{
                const buttons = [...(scopeRoot.querySelectorAll?.('button,input[type="submit"],[role="button"]') || [])]
                  .filter(inPuzzleWindow);
                submitEl = buttons.find(el => confirmRx.test(text(el))) || null;
              }}
              let score = 40;
              score += sourceCount === count ? 25 : Math.round(15*sourceCount/count);
              score += Math.round(15*regular/count);
              score += Math.round(10*clickable/count);
              if (group.override) score += 20;
              if (submitEl) score += 5;
              if (text(instructionEl)) score += 5;
              if (avgSide < 20) score -= 18;
              else if (avgSide < 40) score -= 8;
              else if (avgSide >= 64) score += 4;
              if (!scope.includes('/iframe')) {{
                const viewportHits = rects.filter(r => r.right > 0 && r.bottom > 0 && r.left < viewport.width && r.top < viewport.height).length;
                score += Math.round(12 * viewportHits / count);
                if (viewportHits === 0) score -= 30;
              }}
              candidates.push({{
                kind:'image-grid', scope, score, rows, columns, tileCount:count,
                instruction:text(instructionEl).slice(0,600), sources,
                submitText:text(submitEl).slice(0,120),
                submitBounds:submitEl ? rectOf(submitEl, offset) : null,
                complete, failed, override:group.override, rawMarks, viewport,
              }});
            }}
          }}
          candidates.sort((a,b) => b.score-a.score);
          return candidates[0] || {{
            kind:'none',scope:'document',score:0,rows:0,columns:0,tileCount:0,
            instruction:'',sources:[],submitText:'',submitBounds:null,
            complete:explicitComplete,failed:explicitFailed,override:false,rawMarks:[],viewport
          }};
        }})()
        """
        try:
            value = self._evaluate(script)
        except Exception:
            return self._empty("document")
        return self._normalize(value, default_scope="document")

    def _snapshot_nested_frames(self) -> Dict[str, Any]:
        try:
            frames = list(self._sb.find_elements("iframe") or [])
        except Exception:
            return self._empty("iframe")

        viewport = self._top_level_viewport()
        best = self._empty("iframe")
        for frame_index, frame in enumerate(frames):
            frame_position = self._element_position(frame)
            if not isinstance(frame_position, dict):
                continue
            try:
                frame_x = float(frame_position.get("x") or 0.0)
                frame_y = float(frame_position.get("y") or 0.0)
            except (TypeError, ValueError):
                continue
            try:
                images = [img for img in (frame.query_selector_all("img") or []) if self._element_visible(img)]
            except Exception:
                continue
            if len(images) not in _GRID_SIZES:
                continue
            rows, columns = _GRID_SIZES[len(images)]
            sources = [self._element_image_source(img) for img in images]
            scope = f"iframe:{frame_index}"
            raw_marks = []
            valid = True
            for index, image in enumerate(images):
                alt = self._element_attribute(image, "alt")
                identity = self._element_attribute(image, "id") or self._element_attribute(image, "data-testid") or f"slot:{index}"
                local = self._element_position(image)
                if not isinstance(local, dict):
                    valid = False
                    break
                bounds = dict(local)
                try:
                    bounds["x"] = float(bounds.get("x") or 0.0) + frame_x
                    bounds["y"] = float(bounds.get("y") or 0.0) + frame_y
                except (TypeError, ValueError):
                    valid = False
                    break
                raw_marks.append({
                    "role": "grid-tile",
                    "visualBounds": bounds,
                    "confidence": 0.92 if sources[index] else 0.70,
                    "structuralKey": f"img|{identity}",
                    "semanticSignature": f"grid-tile|{alt}|{sources[index]}",
                    "source": sources[index],
                    "label": alt,
                    "score": index,
                })
            if not valid:
                continue
            marks = build_stable_marks(raw_marks, scope=scope, viewport=viewport)
            score = 55 + (25 if all(sources) else 10)
            candidate = {
                "kind": "image-grid",
                "scope": scope,
                "score": score,
                "rows": rows,
                "columns": columns,
                "tileCount": len(images),
                "instruction": self._frame_descriptor(frame),
                "sources": sources,
                "submitText": "",
                "submitBounds": None,
                "complete": False,
                "failed": False,
                "override": False,
                "viewport": viewport,
                "marks": marks,
            }
            if score > int(best.get("score") or 0):
                best = candidate
        return best

    def _with_generation(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        signature_input = "|".join([
            str(snapshot.get("kind") or "none"),
            str(snapshot.get("scope") or ""),
            str(snapshot.get("tileCount") or 0),
            str(snapshot.get("instruction") or ""),
            str(bool(snapshot.get("complete"))),
            str(bool(snapshot.get("failed"))),
            stable_mark_digest(snapshot.get("marks") or []),
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
        executor = getattr(self._sb, "execute_script", None)
        if callable(executor):
            return executor(f"return {script};")
        raise RuntimeError("SeleniumBase CDP adapter has no script evaluation method")

    def _top_level_viewport(self) -> Dict[str, Any]:
        script = """
        (() => ({
          width: window.innerWidth || document.documentElement.clientWidth || 0,
          height: window.innerHeight || document.documentElement.clientHeight || 0,
          scrollX: window.scrollX || 0,
          scrollY: window.scrollY || 0,
          devicePixelRatio: window.devicePixelRatio || 1,
        }))()
        """
        try:
            value = self._evaluate(script)
        except Exception:
            value = {}
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _clean_overrides(values: Dict[str, str]) -> Dict[str, str]:
        allowed = {"root", "tiles", "instruction", "submit", "complete", "failed"}
        return {key: str(value).strip() for key, value in values.items() if key in allowed and str(value).strip()}

    @staticmethod
    def _normalize(value: Any, *, default_scope: str) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return GridSiteAdapter._empty(default_scope)
        scope = str(value.get("scope") or default_scope)
        viewport = value.get("viewport") if isinstance(value.get("viewport"), dict) else {}
        marks = build_stable_marks(
            [dict(item) for item in value.get("rawMarks") or [] if isinstance(item, dict)],
            scope=scope,
            viewport=viewport,
        )
        submit_bounds = value.get("submitBounds") if isinstance(value.get("submitBounds"), dict) else None
        return {
            "kind": str(value.get("kind") or "none"),
            "scope": scope,
            "score": int(value.get("score") or 0),
            "rows": int(value.get("rows") or 0),
            "columns": int(value.get("columns") or 0),
            "tileCount": int(value.get("tileCount") or 0),
            "instruction": str(value.get("instruction") or ""),
            "sources": [str(item) for item in value.get("sources") or []],
            "submitText": str(value.get("submitText") or ""),
            "submitBounds": dict(submit_bounds) if submit_bounds else None,
            "complete": bool(value.get("complete")),
            "failed": bool(value.get("failed")),
            "override": bool(value.get("override")),
            "viewport": dict(viewport),
            "marks": marks,
        }

    @staticmethod
    def _empty(scope: str) -> Dict[str, Any]:
        return {
            "kind": "none",
            "scope": scope,
            "score": 0,
            "rows": 0,
            "columns": 0,
            "tileCount": 0,
            "instruction": "",
            "sources": [],
            "submitText": "",
            "submitBounds": None,
            "complete": False,
            "failed": False,
            "override": False,
            "viewport": {},
            "marks": [],
        }

    @staticmethod
    def _element_visible(element: Any) -> bool:
        position = GridSiteAdapter._element_position(element)
        if isinstance(position, dict):
            try:
                return float(position.get("width") or 0) > 0 and float(position.get("height") or 0) > 0
            except (TypeError, ValueError):
                return False
        return True

    @staticmethod
    def _element_position(element: Any) -> Dict[str, Any] | None:
        try:
            position = element.get_position()
        except Exception:
            return None
        return dict(position) if isinstance(position, dict) else None

    @staticmethod
    def _element_attribute(element: Any, name: str) -> str:
        try:
            value = element.get_attribute(name)
        except Exception:
            value = None
        return str(value or "")

    @staticmethod
    def _element_image_source(element: Any) -> str:
        for name in ("src", "currentSrc", "data-src"):
            value = GridSiteAdapter._element_attribute(element, name)
            if value:
                return value
        return ""

    @staticmethod
    def _frame_descriptor(frame: Any) -> str:
        parts: List[str] = []
        for name in ("title", "name", "id", "src"):
            value = GridSiteAdapter._element_attribute(frame, name)
            if value:
                parts.append(value)
        return " ".join(parts)[:600]
