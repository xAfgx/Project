from __future__ import annotations

import asyncio
import json
import random
import time
from typing import Any, Dict, Iterable, List


_SUPPORTED_COUNTS = [4, 6, 8, 9, 12, 16, 20, 25]


class AuthorizedGridActionExecutor:
    """Apply classifier-selected stable marks to the current structural test grid."""

    def __init__(self, seleniumbase_cdp: Any, site_adapter: Any) -> None:
        self._sb = seleniumbase_cdp
        self._site_adapter = site_adapter

    def apply(self, indexes: Iterable[int], *, submit: bool = True) -> Dict[str, Any]:
        state = self._site_adapter.poll()
        selected = self._indexes(indexes, int(state.get("tileCount") or 0))
        return self._apply_state(state, selected, submit=submit)

    def apply_marks(self, mark_ids: Iterable[str], *, submit: bool = True) -> Dict[str, Any]:
        state = self._site_adapter.poll()
        requested = {str(value) for value in mark_ids if str(value)}
        marks = [mark for mark in state.get("marks") or [] if isinstance(mark, dict) and mark.get("role") == "grid-tile"]
        selected = [index for index, mark in enumerate(marks) if str(mark.get("markId") or "") in requested]
        result = self._apply_state(state, selected, submit=submit)
        clicked = set(result.get("clickedIndexes") or [])
        result["requestedMarkIds"] = sorted(requested)
        result["clickedMarkIds"] = [
            str(mark.get("markId") or "")
            for index, mark in enumerate(marks)
            if index in clicked and mark.get("markId")
        ]
        return result

    def _apply_state(self, state: Dict[str, Any], selected: List[int], *, submit: bool) -> Dict[str, Any]:
        selected = self._indexes(selected, int(state.get("tileCount") or 0))
        if state.get("kind") != "image-grid" or not selected:
            return {"clickedIndexes": [], "submitted": False, "state": state}

        result = self._apply_document(selected, submit)
        if not result["clicked"] and str(state.get("scope") or "").startswith("iframe:"):
            result = self._apply_frame(state, selected, submit)

        return {
            "clickedIndexes": result["clicked"],
            "submitted": result["submitted"],
            "state": self._site_adapter.poll(),
        }

    def _apply_document(self, selected: List[int], submit: bool) -> Dict[str, Any]:
        overrides = getattr(self._site_adapter, "_overrides", {})
        script = f"""
        (() => {{
          const selected = {json.dumps(selected)};
          const overrides = {json.dumps(overrides)};
          const supported = new Set({json.dumps(_SUPPORTED_COUNTS)});
          const seen = new Set();
          const visible = el => {{
            if (!el?.getBoundingClientRect) return false;
            const r = el.getBoundingClientRect(), s = getComputedStyle(el);
            return r.width >= 24 && r.height >= 24 && s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
          }};
          const bgUrl = el => {{
            if (!el || !visible(el)) return '';
            const bg = getComputedStyle(el).backgroundImage || '';
            const match = bg.match(/url\\(["']?(.*?)["']?\\)/i);
            return match?.[1] || '';
          }};
          const tileFor = visual => visual.closest?.('button,[role="button"],[tabindex],label,li,[class*="tile" i],[class*="cell" i]') || visual;
          const visualsIn = root => {{
            const items = [...(root.querySelectorAll?.('img,canvas') || [])].filter(visible);
            const bgCandidates = [...(root.querySelectorAll?.('button,[role="button"],[tabindex],label,li,[class*="tile" i],[class*="cell" i],[class*="image" i]') || [])]
              .filter(el => visible(el) && bgUrl(el));
            return [...new Set([...items, ...bgCandidates])];
          }};
          const groups = [];
          const walk = (root, ox, oy) => {{
            if (!root || seen.has(root)) return;
            seen.add(root);
            if (overrides.tiles) {{
              const tiles = [...root.querySelectorAll(overrides.tiles)].filter(visible);
              if (supported.has(tiles.length)) groups.push({{root:overrides.root ? root.querySelector(overrides.root) || root : root, tiles, offsetX:ox, offsetY:oy, preferred:true}});
            }}
            if (!overrides.tiles) {{
              const parents = new Set();
              for (const visual of visualsIn(root)) {{
                let node = tileFor(visual);
                for (let depth=0; node && depth<5; depth++, node=node.parentElement) if (node.parentElement) parents.add(node.parentElement);
              }}
              for (const parent of parents) {{
                const tiles = [...new Set(visualsIn(parent).map(tileFor))].filter(visible);
                if (!supported.has(tiles.length)) continue;
                groups.push({{root:parent, tiles, offsetX:ox, offsetY:oy, preferred:false}});
              }}
            }}
            for (const el of root.querySelectorAll?.('*') || []) if (el.shadowRoot) walk(el.shadowRoot, ox, oy);
            for (const frame of root.querySelectorAll?.('iframe') || []) {{
              try {{
                if (frame.contentDocument) {{
                  const r = frame.getBoundingClientRect();
                  walk(frame.contentDocument, ox + r.left, oy + r.top);
                }}
              }} catch (_) {{}}
            }}
          }};
          walk(document, 0, 0);
          groups.sort((a,b) => Number(b.preferred)-Number(a.preferred));
          const group = groups[0];
          if (!group) return {{tilePoints:[], submitPoint:null}};

          const tilePoints = [];
          for (const index of selected) {{
            const tile = group.tiles[index];
            if (!tile) continue;
            tile.scrollIntoView({{block:'center', inline:'center'}});
            const r = tile.getBoundingClientRect();
            tilePoints.push({{index, x: group.offsetX + r.left + r.width/2, y: group.offsetY + r.top + r.height/2}});
          }}

          let submitPoint = null;
          if ({str(submit).lower()}) {{
            const gridRect = group.tiles.reduce((acc, tile) => {{
              const r = tile.getBoundingClientRect();
              return {{left:Math.min(acc.left,r.left),top:Math.min(acc.top,r.top),right:Math.max(acc.right,r.right),bottom:Math.max(acc.bottom,r.bottom)}};
            }}, {{left:Infinity,top:Infinity,right:-Infinity,bottom:-Infinity}});
            const inPuzzleWindow = el => {{
              if (!el || !visible(el) || group.tiles.includes(el)) return false;
              const r = el.getBoundingClientRect();
              const cx = r.left + r.width / 2;
              return r.top >= gridRect.top - 12 && r.top <= gridRect.bottom + 260
                && cx >= gridRect.left - 80 && cx <= gridRect.right + 80;
            }};
            let button = null;
            if (overrides.submit) {{
              const exact = document.querySelector(overrides.submit);
              if (exact && visible(exact)) button = exact;
            }}
            if (!button) {{
              const verify = document.querySelector('#recaptcha-verify-button, .rc-button-default');
              if (inPuzzleWindow(verify)) button = verify;
            }}
            if (!button) {{
              const selector = 'button[type="submit"],input[type="submit"],button,[role="button"]';
              button = [...(group.root.querySelectorAll(selector) || [])].filter(inPuzzleWindow)[0] || null;
            }}
            if (button) {{
              const r = button.getBoundingClientRect();
              submitPoint = {{x: group.offsetX + r.left + r.width/2, y: group.offsetY + r.top + r.height/2}};
            }}
          }}
          return {{tilePoints, submitPoint}};
        }})()
        """
        value = self._evaluate(script)
        if not isinstance(value, dict):
            return {"clicked": [], "submitted": False}
        clicked: List[int] = []
        for point in value.get("tilePoints") or []:
            if not isinstance(point, dict):
                continue
            try:
                index = int(point.get("index"))
                x = float(point.get("x"))
                y = float(point.get("y"))
            except (TypeError, ValueError):
                continue
            if self._cdp_click(x, y):
                clicked.append(index)
        submitted = False
        submit_point = value.get("submitPoint")
        if isinstance(submit_point, dict):
            try:
                submitted = self._cdp_click(float(submit_point.get("x")), float(submit_point.get("y")))
            except (TypeError, ValueError):
                submitted = False
        return {"clicked": clicked, "submitted": submitted}

    def _cdp_click(self, x: float, y: float) -> bool:
        """Native CDP press/release at top-level viewport coordinates.

        Replaces the old JavaScript el.click() fallback: a scripted click is
        untrusted (detail 0, origin coordinates, no press behind it) and is
        exactly what bot detection flags. This path goes through the real input
        pipeline, so the press/release pair is complete and trusted.
        """
        try:
            from mycdp import input_ as cdp_input
        except Exception:
            return False
        tab = getattr(self._sb, "get_active_tab", None)
        loop = getattr(self._sb, "get_event_loop", None)
        if not callable(tab) or not callable(loop):
            return False
        try:
            active = tab()
            event_loop = loop()
            button = cdp_input.MouseButton("left")
            px, py = float(x), float(y)
            event_loop.run_until_complete(active.send(cdp_input.dispatch_mouse_event("mouseMoved", x=px, y=py, button=button, buttons=0, pointer_type="mouse")))
            event_loop.run_until_complete(active.send(cdp_input.dispatch_mouse_event("mousePressed", x=px, y=py, button=button, buttons=1, click_count=1, pointer_type="mouse")))
            time.sleep(random.uniform(0.045, 0.115))
            event_loop.run_until_complete(active.send(cdp_input.dispatch_mouse_event("mouseReleased", x=px, y=py, button=button, buttons=0, click_count=1, pointer_type="mouse")))
            return True
        except Exception:
            return False

    def _apply_frame(self, state: Dict[str, Any], selected: List[int], submit: bool) -> Dict[str, Any]:
        try:
            frame_index = int(str(state["scope"]).split(":", 1)[1])
            frame = list(self._sb.find_elements("iframe") or [])[frame_index]
            images = list(frame.query_selector_all("img") or [])
        except Exception:
            return {"clicked": [], "submitted": False}

        clicked = []
        for index in selected:
            if index >= len(images):
                continue
            click = getattr(images[index], "mouse_click", None)
            if callable(click):
                click()
                self._flush_pending_input()
                clicked.append(index)

        submitted = False
        if submit:
            overrides = getattr(self._site_adapter, "_overrides", {})
            for selector in (overrides.get("submit"), 'button[type="submit"]', 'input[type="submit"]', 'button'):
                if not selector:
                    continue
                try:
                    button = frame.query_selector(selector)
                except Exception:
                    button = None
                click = getattr(button, "mouse_click", None) if button else None
                if callable(click):
                    click()
                    self._flush_pending_input()
                    submitted = True
                    break
        return {"clicked": clicked, "submitted": submitted}

    def _flush_pending_input(self) -> None:
        """Run SeleniumBase's fire-and-forget mouseReleased task immediately.

        `element.mouse_click()` schedules the native mouseReleased as an
        un-awaited asyncio task, and `run_until_complete` returns before that
        task executes. The click then lands seconds later (or after the next
        press) and is counted as a click with no button press behind it. Pumping
        the loop once keeps the press/release pair inside the same interaction.
        """
        loop = getattr(self._sb, "get_event_loop", None)
        if not callable(loop):
            return
        try:
            loop().run_until_complete(asyncio.sleep(0.05))
        except Exception:
            pass

    def _evaluate(self, script: str) -> Any:
        evaluate = getattr(self._sb, "evaluate", None)
        return evaluate(script) if callable(evaluate) else self._sb.execute_script(f"return {script};")

    @staticmethod
    def _indexes(values: Iterable[int], count: int) -> List[int]:
        clean = {int(value) for value in values}
        return sorted(index for index in clean if 0 <= index < count)
