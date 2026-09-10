from __future__ import annotations

import json
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
          const roots = [], seen = new Set();
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
          const walk = root => {{
            if (!root || seen.has(root)) return;
            seen.add(root); roots.push(root);
            for (const el of root.querySelectorAll?.('*') || []) if (el.shadowRoot) walk(el.shadowRoot);
            for (const frame of root.querySelectorAll?.('iframe') || []) {{
              try {{ if (frame.contentDocument) walk(frame.contentDocument); }} catch (_) {{}}
            }}
          }};
          walk(document);

          const groups = [];
          for (const root of roots) {{
            if (overrides.tiles) {{
              const tiles = [...root.querySelectorAll(overrides.tiles)].filter(visible);
              if (supported.has(tiles.length)) groups.push({{root:overrides.root ? root.querySelector(overrides.root) || root : root, tiles, preferred:true}});
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
                groups.push({{root:parent, tiles, preferred:false}});
              }}
            }}
          }}
          groups.sort((a,b) => Number(b.preferred)-Number(a.preferred));
          const group = groups[0];
          if (!group) return {{clicked:[], submitted:false}};

          const clicked = [];
          for (const index of selected) {{
            const tile = group.tiles[index];
            if (!tile) continue;
            tile.scrollIntoView({{block:'center', inline:'center'}});
            tile.click();
            clicked.push(index);
          }}

          let submitted = false;
          if ({str(submit).lower()}) {{
            const selector = overrides.submit || 'button[type="submit"],input[type="submit"],button,[role="button"]';
            const button = [...(group.root.parentElement?.querySelectorAll(selector) || [])]
              .filter(el => visible(el) && !group.tiles.includes(el))[0];
            if (button) {{ button.click(); submitted = true; }}
          }}
          return {{clicked, submitted}};
        }})()
        """
        value = self._evaluate(script)
        return value if isinstance(value, dict) else {"clicked": [], "submitted": False}

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
            click = getattr(images[index], "mouse_click", None) or getattr(images[index], "click", None)
            if callable(click):
                click()
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
                click = (getattr(button, "mouse_click", None) or getattr(button, "click", None)) if button else None
                if callable(click):
                    click()
                    submitted = True
                    break
        return {"clicked": clicked, "submitted": submitted}

    def _evaluate(self, script: str) -> Any:
        evaluate = getattr(self._sb, "evaluate", None)
        return evaluate(script) if callable(evaluate) else self._sb.execute_script(f"return {script};")

    @staticmethod
    def _indexes(values: Iterable[int], count: int) -> List[int]:
        clean = {int(value) for value in values}
        return sorted(index for index in clean if 0 <= index < count)
