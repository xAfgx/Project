from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Tuple

from site_grid_adapter import GridSiteAdapter
from stable_marks import build_stable_marks

_ACTION_CONTEXT_RE = re.compile(
    r"(?i)(select|click|choose|mark|verify|verification|continue|confirm|"
    r"wähl|waehl|klick|markier|prüf|pruef|bestät|bestaet|weiter)"
)

_OOPIF_GRID_SCRIPT = r"""
return (() => {
  const overrides = arguments[0] || {};
  const MIN_DIM = 2, MAX_DIM = 8, MIN_COUNT = 4, MAX_COUNT = 64;
  const viewport = {
    width: window.innerWidth || document.documentElement.clientWidth || 0,
    height: window.innerHeight || document.documentElement.clientHeight || 0,
    scrollX: window.scrollX || 0,
    scrollY: window.scrollY || 0,
    devicePixelRatio: window.devicePixelRatio || 1,
  };
  const visible = el => {
    if (!el?.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0
      && r.right > 0 && r.bottom > 0
      && r.left < viewport.width && r.top < viewport.height
      && s.display !== 'none'
      && s.visibility !== 'hidden'
      && Number(s.opacity || 1) > 0;
  };
  const visualReady = el => {
    if (!el) return false;
    if (el instanceof HTMLImageElement) return Boolean(el.complete && el.naturalWidth > 0 && el.naturalHeight > 0);
    if (el instanceof HTMLCanvasElement) return el.width > 0 && el.height > 0;
    return true;
  };
  const text = el => (el?.innerText || el?.textContent || el?.getAttribute?.('aria-label') || '')
    .trim().replace(/\s+/g, ' ');
  const rectOf = el => {
    const r = el.getBoundingClientRect();
    return {x:r.x,y:r.y,width:r.width,height:r.height};
  };
  const selectorFor = el => {
    if (!el) return '';
    if (el.id) return '#' + CSS.escape(el.id);
    const testId = el.getAttribute?.('data-testid');
    if (testId) return '[data-testid="' + CSS.escape(testId) + '"]';
    return '';
  };
  const bgUrl = el => {
    if (!el || !visible(el)) return '';
    const bg = getComputedStyle(el).backgroundImage || '';
    const match = bg.match(/url\(["']?(.*?)["']?\)/i);
    return match?.[1] || '';
  };
  const sourceOf = tile => {
    const img = tile?.matches?.('img') ? tile : tile?.querySelector?.('img');
    if (img) return img.currentSrc || img.src || img.getAttribute('src') || img.getAttribute('data-src') || '';
    const canvas = tile?.matches?.('canvas') ? tile : tile?.querySelector?.('canvas');
    if (canvas) {
      try { return canvas.toDataURL?.('image/png') || ''; } catch (_) { return ''; }
    }
    return bgUrl(tile);
  };
  const tileFor = visual => {
    if (visual?.matches?.('img') && /rc-image-tile|rc-imageselect/i.test(String(visual.className || ''))) {
      const cell = visual.closest?.('td.rc-imageselect-tile,[class*="rc-imageselect-tile" i]');
      if (cell) return cell;
    }
    return visual.closest?.('button,[role="button"],[tabindex],label,li,[class*="tile" i],[class*="cell" i]') || visual;
  };
  const isControl = el => /^recaptcha-/i.test(String(el?.id || el?.getAttribute?.('id') || ''));
  const visualsIn = root => {
    const direct = [...(root.querySelectorAll?.('img,canvas') || [])].filter(el => visible(el) && visualReady(el) && !isControl(el));
    const backgrounds = [...(root.querySelectorAll?.(
      'button,[role="button"],[tabindex],label,li,[class*="tile" i],[class*="cell" i],[class*="image" i]'
    ) || [])].filter(el => visible(el) && bgUrl(el) && !isControl(el));
    return [...new Set([...direct, ...backgrounds])];
  };
  const clusterCount = (values, tolerance) => {
    const sorted = [...values].sort((a,b) => a-b);
    const clusters = [];
    for (const value of sorted) {
      const last = clusters[clusters.length - 1];
      if (!last || Math.abs(value - last.mean) > tolerance) {
        clusters.push({mean:value,count:1});
      } else {
        last.mean = (last.mean * last.count + value) / (last.count + 1);
        last.count += 1;
      }
    }
    return clusters.length;
  };
  const inferShape = tiles => {
    const rects = tiles.map(el => el.getBoundingClientRect());
    const avgW = rects.reduce((a,r) => a+r.width,0) / rects.length;
    const avgH = rects.reduce((a,r) => a+r.height,0) / rects.length;
    const rows = clusterCount(rects.map(r => r.top + r.height/2), Math.max(6, Math.min(36, avgH * 0.45)));
    const columns = clusterCount(rects.map(r => r.left + r.width/2), Math.max(6, Math.min(36, avgW * 0.45)));
    if (rows < MIN_DIM || columns < MIN_DIM || rows > MAX_DIM || columns > MAX_DIM) return null;
    if (rows * columns !== tiles.length) return null;
    const regular = rects.filter(r =>
      Math.abs(r.width-avgW) <= Math.max(10, avgW*.28)
      && Math.abs(r.height-avgH) <= Math.max(10, avgH*.28)
    ).length;
    if (regular / tiles.length < 0.82) return null;
    return {rows, columns, regular, avgW, avgH};
  };
  const instructionNear = groupRoot => {
    if (overrides.instruction) {
      const exact = document.querySelector(overrides.instruction);
      if (exact) return exact;
    }
    if (groupRoot?.previousElementSibling && visible(groupRoot.previousElementSibling)) {
      return groupRoot.previousElementSibling;
    }
    const parent = groupRoot?.parentElement;
    if (!parent) return null;
    return [...parent.querySelectorAll(
      'h1,h2,h3,h4,p,[class*="instruction" i],[class*="prompt" i],[class*="question" i]'
    )].find(visible) || null;
  };
  const referenceFor = (tiles, instructionEl) => {
    const tileSet = new Set(tiles);
    const refLike = /(reference|referenz|prompt|question|frage|aufgabe|beispiel|example|sample|target|goal|original)/i;
    const direct = [...(document.querySelectorAll?.('img,canvas,[role="img"]') || [])]
      .filter(el => visible(el) && !tileSet.has(el));
    const backgrounds = [...(document.querySelectorAll?.('*') || [])].filter(el => {
      if (!visible(el) || tileSet.has(el) || !bgUrl(el)) return false;
      const r = el.getBoundingClientRect();
      return r.width >= 30 && r.height >= 30 && r.width <= 600 && r.height <= 600;
    });
    const scored = [];
    for (const el of [...new Set([...direct, ...backgrounds])]) {
      const identity = (
        (el.id || '') + ' ' +
        (typeof el.className === 'string' ? el.className : '') + ' ' +
        (el.getAttribute?.('aria-label') || '') + ' ' +
        (el.getAttribute?.('alt') || '')
      ).toLowerCase();
      const r = el.getBoundingClientRect();
      let score = 0;
      if (refLike.test(identity)) score += 1000;
      const instructionRoot = instructionEl?.parentElement || instructionEl;
      if (instructionRoot && (instructionRoot === el || instructionRoot.contains(el))) score += 400;
      if (instructionEl?.previousElementSibling && instructionEl.previousElementSibling === el) score += 300;
      if (tiles.length) {
        const gridTop = Math.min(...tiles.map(t => t.getBoundingClientRect().top));
        if (r.bottom <= gridTop + 12) score += 250;
      }
      score += Math.min(200, Math.round((r.width * r.height) / 100));
      if (score > 0) scored.push({el, score});
    }
    if (!scored.length) return null;
    scored.sort((a, b) => b.score - a.score);
    const best = scored[0];
    return {bounds: rectOf(best.el), source: sourceOf(best.el)};
  };
  const submitFor = tiles => {
    const tileSet = new Set(tiles);
    const confirmRx = /(verify|submit|bestätig|bestaetig|weiter|continue|prüf|pruef|überprüfen|ueberpruefen|absenden|senden|fertig|abschließ|abschliess|confirm|check|done|next|ok|ja|yes)/i;
    const challengeRx = /(recaptcha|rc-imageselect|challenge|captcha)/i;
    const gridRect = tiles.reduce((acc, tile) => {
      const r = tile.getBoundingClientRect();
      return {left:Math.min(acc.left,r.left),top:Math.min(acc.top,r.top),right:Math.max(acc.right,r.right),bottom:Math.max(acc.bottom,r.bottom)};
    }, {left:Infinity,top:Infinity,right:-Infinity,bottom:-Infinity});
    let scopeRoot = document.body || document;
    for (let node = tiles[0]; node && node !== document; node = node.parentElement) {
      const cls = typeof node.className === 'string' ? node.className : '';
      if (challengeRx.test(String(node.id || '') + ' ' + cls)) { scopeRoot = node; break; }
    }
    const inPuzzleWindow = el => {
      if (!el || !visible(el) || tileSet.has(el)) return false;
      if (scopeRoot.contains && el !== scopeRoot && !scopeRoot.contains(el)) return false;
      const r = el.getBoundingClientRect();
      const cx = r.left + r.width / 2;
      return r.top >= gridRect.top - 12 && r.top <= gridRect.bottom + 260
        && cx >= gridRect.left - 80 && cx <= gridRect.right + 80;
    };
    if (overrides.submit) {
      const exact = document.querySelector(overrides.submit);
      if (exact && inPuzzleWindow(exact)) return exact;
    }
    const verify = scopeRoot.querySelector?.('#recaptcha-verify-button, .rc-button-default');
    if (inPuzzleWindow(verify)) return verify;
    const buttons = [...(scopeRoot.querySelectorAll?.('button,input[type="submit"],[role="button"]') || [])]
      .filter(inPuzzleWindow);
    return buttons.find(el => confirmRx.test(text(el)))
      || buttons.find(el => el.matches?.('button[type="submit"],input[type="submit"]'))
      || null;
  };
  const complete = Boolean(overrides.complete && visible(document.querySelector(overrides.complete)));
  const failed = Boolean(overrides.failed && visible(document.querySelector(overrides.failed)));
  const actionRx = /(select|click|choose|mark|verify|verification|continue|confirm|wähl|waehl|klick|markier|prüf|pruef|bestät|bestaet|weiter)/i;
  const parents = new Set();
  for (const visual of visualsIn(document)) {
    let node = tileFor(visual);
    for (let depth=0; node && depth<5; depth++, node=node.parentElement) {
      if (node.parentElement) parents.add(node.parentElement);
    }
  }
  const candidates = [];
  for (const parent of parents) {
    const tiles = [...new Set(visualsIn(parent).map(tileFor))].filter(visible);
    const count = tiles.length;
    if (count < MIN_COUNT || count > MAX_COUNT) continue;
    const shape = inferShape(tiles);
    if (!shape) continue;
    const sources = tiles.map(sourceOf);
    const sourceCount = sources.filter(Boolean).length;
    const instructionEl = instructionNear(parent);
    const submitEl = submitFor(tiles);
    const instruction = text(instructionEl).slice(0,600);
    const submitText = text(submitEl).slice(0,120);
    const hasActionContext = actionRx.test(instruction + ' ' + submitText);
    const reference = referenceFor(tiles, instructionEl);
    const rawMarks = tiles.map((tile,index) => ({
      role:'grid-tile',
      visualBounds:rectOf(tile),
      confidence:Math.max(0.70, Math.min(0.98, 0.78 + (sources[index] ? 0.16 : 0))),
      selector:selectorFor(tile),
      structuralKey:[
        'grid-tile',
        tile?.tagName || '',
        tile?.getAttribute?.('id') || '',
        tile?.getAttribute?.('data-testid') || '',
        tile?.getAttribute?.('aria-label') || '',
        `slot:${index}`,
      ].join('|'),
      semanticSignature:['grid-tile', text(tile).slice(0,160), sources[index]].join('|'),
      source:sources[index],
      label:text(tile).slice(0,160),
      score:index,
    }));
    const submitBounds = submitEl && visible(submitEl) ? rectOf(submitEl) : null;
    const avgSide = Math.min(shape.avgW, shape.avgH);
    let score = 72;
    score += Math.round(14 * sourceCount / count);
    score += Math.round(10 * shape.regular / count);
    if (hasActionContext) score += 8;
    else if (instruction) score += 1;
    if (submitEl) score += 3;
    if (avgSide < 20) score -= 18;
    else if (avgSide < 40) score -= 8;
    else if (avgSide >= 64) score += 4;
    candidates.push({
      kind:'image-grid',
      scope:'oopif',
      score,
      rows:shape.rows,
      columns:shape.columns,
      tileCount:count,
      instruction,
      sources,
      submitText,
      submitBounds,
      referenceBounds: reference ? reference.bounds : null,
      referenceSource: reference ? reference.source : '',
      complete,
      failed,
      override:false,
      rawMarks,
      viewport,
    });
  }
  candidates.sort((a,b) => b.score-a.score);
  return candidates[0] || {
    kind:'none',scope:'oopif',score:0,rows:0,columns:0,tileCount:0,
    instruction:'',sources:[],submitText:'',submitBounds:null,complete,failed,
    override:false,rawMarks:[],viewport
  };
})();
"""


class ExtendedGridSiteAdapter(GridSiteAdapter):
    """Grid adapter with broad visual discovery, ranked false-positive suppression, and OOPIF routing."""

    MIN_DIM = 2
    MAX_DIM = 8
    MIN_COUNT = MIN_DIM * MIN_DIM
    MAX_COUNT = MAX_DIM * MAX_DIM
    SOFT_TILE_SIDE = 40.0
    MIN_VIEWPORT_RATIO = 0.55

    def poll(self) -> Dict[str, Any]:
        # Explicit adapter overrides are authoritative. Re-snapshot the exact
        # configured grid on every validation call so visual mutations are still
        # detected, but do not also scan every generic document/frame/OOPIF
        # producer. Large grids otherwise multiply the cost of each pre-click
        # stale-state check and can starve the worker control loop.
        if self._overrides.get("tiles"):
            explicit = self._snapshot_document()
            if bool(explicit.get("complete")) or bool(explicit.get("failed")):
                return self._with_generation(explicit)
            if bool(explicit.get("override")) and self._candidate_is_plausible(explicit):
                return self._with_generation(explicit)

        producers = (
            self._snapshot_document,
            self._snapshot_extended_document,
            self._snapshot_nested_frames,
            self._snapshot_extended_frames,
            self._snapshot_oopif_frames,
        )
        rejected: Dict[str, Any] | None = None
        outcome: Dict[str, Any] | None = None
        best: Dict[str, Any] | None = None
        best_rank = float("-inf")
        for producer in producers:
            snapshot = producer()
            if snapshot.get("kind") == "none":
                if bool(snapshot.get("complete")) or bool(snapshot.get("failed")):
                    outcome = snapshot
                continue
            if not self._candidate_is_plausible(snapshot):
                rejected = snapshot
                continue
            rank = self._candidate_rank(snapshot)
            if best is None or rank > best_rank:
                best = snapshot
                best_rank = rank
        if outcome is not None:
            return self._with_generation(outcome)
        if best is not None:
            return self._with_generation(best)
        scope = str((rejected or {}).get("scope") or "document")
        return self._with_generation(self._empty(scope))

    def _candidate_is_plausible(self, snapshot: Dict[str, Any]) -> bool:
        if snapshot.get("kind") != "image-grid":
            return False
        if bool(snapshot.get("override")):
            return True

        marks = [
            mark for mark in snapshot.get("marks") or []
            if isinstance(mark, dict) and mark.get("role") == "grid-tile"
        ]
        if len(marks) < self.MIN_COUNT:
            return False

        viewport_known = 0
        in_viewport = 0
        for mark in marks:
            bounds = mark.get("visualBounds")
            if not isinstance(bounds, dict):
                continue
            try:
                x = float(bounds.get("x") or 0.0)
                y = float(bounds.get("y") or 0.0)
                width = float(bounds.get("width") or 0.0)
                height = float(bounds.get("height") or 0.0)
            except (TypeError, ValueError):
                continue
            if width <= 0 or height <= 0:
                continue

            viewport = mark.get("viewport")
            if isinstance(viewport, dict):
                try:
                    vw = float(viewport.get("width") or 0.0)
                    vh = float(viewport.get("height") or 0.0)
                except (TypeError, ValueError):
                    vw = vh = 0.0
                if vw > 0 and vh > 0:
                    viewport_known += 1
                    if x + width > 0 and y + height > 0 and x < vw and y < vh:
                        in_viewport += 1

        if viewport_known:
            required = max(self.MIN_COUNT, math.ceil(viewport_known * self.MIN_VIEWPORT_RATIO))
            if in_viewport < required:
                return False
        return True

    def _candidate_rank(self, snapshot: Dict[str, Any]) -> float:
        rank = float(snapshot.get("score") or 0.0)
        marks = [
            mark for mark in snapshot.get("marks") or []
            if isinstance(mark, dict) and mark.get("role") == "grid-tile"
        ]
        sides: List[float] = []
        for mark in marks:
            bounds = mark.get("visualBounds")
            if not isinstance(bounds, dict):
                continue
            try:
                width = float(bounds.get("width") or 0.0)
                height = float(bounds.get("height") or 0.0)
            except (TypeError, ValueError):
                continue
            if width > 0 and height > 0:
                sides.append(min(width, height))
        if sides:
            ordered = sorted(sides)
            middle = len(ordered) // 2
            median_side = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2.0
            if median_side < 20.0:
                rank -= 18.0
            elif median_side < self.SOFT_TILE_SIDE:
                rank -= 8.0
            elif median_side >= 64.0:
                rank += 4.0

        context = " ".join([
            str(snapshot.get("instruction") or ""),
            str(snapshot.get("submitText") or ""),
        ])
        if _ACTION_CONTEXT_RE.search(context):
            rank += 8.0
        elif context.strip():
            rank += 1.0
        return rank

    def _snapshot_extended_document(self) -> Dict[str, Any]:
        overrides = json.dumps(self._overrides)
        script = f"""
        (() => {{
          const overrides = {overrides};
          const MIN_DIM = 2, MAX_DIM = 8, MIN_COUNT = 4, MAX_COUNT = 64;
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
            return r.width > 0 && r.height > 0
              && r.right > 0 && r.bottom > 0
              && r.left < viewport.width && r.top < viewport.height
              && s.display !== 'none'
              && s.visibility !== 'hidden'
              && Number(s.opacity || 1) > 0;
          }};
          const visualReady = el => {{
            if (!el) return false;
            if (el instanceof HTMLImageElement) return Boolean(el.complete && el.naturalWidth > 0 && el.naturalHeight > 0);
            if (el instanceof HTMLCanvasElement) return el.width > 0 && el.height > 0;
            return true;
          }};
          const text = el => (el?.innerText || el?.textContent || el?.getAttribute?.('aria-label') || '')
            .trim().replace(/\\s+/g, ' ');
          const rectOf = el => {{
            const r = el.getBoundingClientRect();
            return {{x:r.x,y:r.y,width:r.width,height:r.height}};
          }};
          const bgUrl = el => {{
            if (!el || !visible(el)) return '';
            const bg = getComputedStyle(el).backgroundImage || '';
            const match = bg.match(/url\\(["']?(.*?)["']?\\)/i);
            return match?.[1] || '';
          }};
          const sourceOf = tile => {{
            const img = tile?.matches?.('img') ? tile : tile?.querySelector?.('img');
            if (img) return img.currentSrc || img.src || img.getAttribute('src') || img.getAttribute('data-src') || '';
            const canvas = tile?.matches?.('canvas') ? tile : tile?.querySelector?.('canvas');
            if (canvas) {{
              try {{ return canvas.toDataURL?.('image/png') || ''; }} catch (_) {{ return ''; }}
            }}
            return bgUrl(tile);
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
            const direct = [...(root.querySelectorAll?.('img,canvas') || [])].filter(el => visible(el) && visualReady(el) && !isControl(el));
            const backgrounds = [...(root.querySelectorAll?.(
              'button,[role="button"],[tabindex],label,li,[class*="tile" i],[class*="cell" i],[class*="image" i]'
            ) || [])].filter(el => visible(el) && bgUrl(el) && !isControl(el));
            return [...new Set([...direct, ...backgrounds])];
          }};
          const clusterCount = (values, tolerance) => {{
            const sorted = [...values].sort((a,b) => a-b);
            const clusters = [];
            for (const value of sorted) {{
              const last = clusters[clusters.length - 1];
              if (!last || Math.abs(value - last.mean) > tolerance) clusters.push({{mean:value,count:1}});
              else {{
                last.mean = (last.mean * last.count + value) / (last.count + 1);
                last.count += 1;
              }}
            }}
            return clusters.length;
          }};
          const inferShape = tiles => {{
            const rects = tiles.map(el => el.getBoundingClientRect());
            const avgW = rects.reduce((a,r) => a+r.width,0) / rects.length;
            const avgH = rects.reduce((a,r) => a+r.height,0) / rects.length;
            const rows = clusterCount(rects.map(r => r.top + r.height/2), Math.max(6, Math.min(36, avgH * 0.45)));
            const columns = clusterCount(rects.map(r => r.left + r.width/2), Math.max(6, Math.min(36, avgW * 0.45)));
            if (rows < MIN_DIM || columns < MIN_DIM || rows > MAX_DIM || columns > MAX_DIM) return null;
            if (rows * columns !== tiles.length) return null;
            const regular = rects.filter(r =>
              Math.abs(r.width-avgW) <= Math.max(10, avgW*.28)
              && Math.abs(r.height-avgH) <= Math.max(10, avgH*.28)
            ).length;
            return regular / tiles.length >= 0.82 ? {{rows, columns, regular, avgW, avgH}} : null;
          }};
          const actionRx = /(select|click|choose|mark|verify|verification|continue|confirm|wähl|waehl|klick|markier|prüf|pruef|bestät|bestaet|weiter)/i;
          const parents = new Set();
          for (const visual of visualsIn(document)) {{
            let node = tileFor(visual);
            for (let depth=0; node && depth<5; depth++, node=node.parentElement) {{
              if (node.parentElement) parents.add(node.parentElement);
            }}
          }}
          const complete = Boolean(overrides.complete && visible(document.querySelector(overrides.complete)));
          const failed = Boolean(overrides.failed && visible(document.querySelector(overrides.failed)));
          const candidates = [];
          for (const parent of parents) {{
            const tiles = [...new Set(visualsIn(parent).map(tileFor))].filter(visible);
            if (tiles.length < MIN_COUNT || tiles.length > MAX_COUNT) continue;
            const shape = inferShape(tiles);
            if (!shape) continue;
            const sources = tiles.map(sourceOf);
            const instructionEl = overrides.instruction
              ? document.querySelector(overrides.instruction)
              : parent.previousElementSibling || parent.parentElement?.querySelector(
                  'h1,h2,h3,h4,p,[class*="instruction" i],[class*="prompt" i],[class*="question" i]'
                );
            const confirmRx = /(verify|submit|bestätig|bestaetig|weiter|continue|prüf|pruef|überprüfen|ueberpruefen|absenden|senden|fertig|abschließ|abschliess|confirm|check|done|next|ok|ja|yes)/i;
            const challengeRx = /(recaptcha|rc-imageselect|challenge|captcha)/i;
            const gridRect = tiles.reduce((acc, tile) => {{
              const r = tile.getBoundingClientRect();
              return {{left:Math.min(acc.left,r.left),top:Math.min(acc.top,r.top),right:Math.max(acc.right,r.right),bottom:Math.max(acc.bottom,r.bottom)}};
            }}, {{left:Infinity,top:Infinity,right:-Infinity,bottom:-Infinity}});
            let scopeRoot = parent.parentElement || parent;
            for (let node = tiles[0]; node && node !== document; node = node.parentElement) {{
              const cls = typeof node.className === 'string' ? node.className : '';
              if (challengeRx.test(String(node.id || '') + ' ' + cls)) {{ scopeRoot = node; break; }}
            }}
            const inPuzzleWindow = el => {{
              if (!el || !visible(el) || tiles.includes(el)) return false;
              if (scopeRoot.contains && el !== scopeRoot && !scopeRoot.contains(el)) return false;
              const r = el.getBoundingClientRect();
              const cx = r.left + r.width / 2;
              return r.top >= gridRect.top - 12 && r.top <= gridRect.bottom + 260
                && cx >= gridRect.left - 80 && cx <= gridRect.right + 80;
            }};
            const findSubmit = () => {{
              if (overrides.submit) {{
                const exact = document.querySelector(overrides.submit);
                if (exact && inPuzzleWindow(exact)) return exact;
              }}
              const verify = scopeRoot.querySelector?.('#recaptcha-verify-button, .rc-button-default');
              if (inPuzzleWindow(verify)) return verify;
              const buttons = [...(scopeRoot.querySelectorAll?.('button,input[type="submit"],[role="button"]') || [])]
                .filter(inPuzzleWindow);
              return buttons.find(el => confirmRx.test(text(el)))
                || buttons.find(el => el.matches?.('button[type="submit"],input[type="submit"]'))
                || null;
            }};
            const submitEl = findSubmit();
            const instruction = text(instructionEl).slice(0,600);
            const submitText = text(submitEl).slice(0,120);
            const hasActionContext = actionRx.test(instruction + ' ' + submitText);
            const rawMarks = tiles.map((tile,index) => ({{
              role:'grid-tile',
              visualBounds:rectOf(tile),
              confidence:0.90,
              selector:'',
              structuralKey:['grid-tile',tile?.tagName||'',`slot:${{index}}`].join('|'),
              semanticSignature:['grid-tile',text(tile).slice(0,160),sources[index]].join('|'),
              source:sources[index],
              label:text(tile).slice(0,160),
              score:index,
            }}));
            const avgSide = Math.min(shape.avgW, shape.avgH);
            let score = 72 + Math.round(14 * sources.filter(Boolean).length / tiles.length);
            if (hasActionContext) score += 8;
            else if (instruction) score += 1;
            if (submitEl) score += 3;
            if (avgSide < 20) score -= 18;
            else if (avgSide < 40) score -= 8;
            else if (avgSide >= 64) score += 4;
            candidates.push({{
              kind:'image-grid',scope:'document',score,rows:shape.rows,columns:shape.columns,
              tileCount:tiles.length,instruction,sources,submitText,
              submitBounds:submitEl && visible(submitEl) ? rectOf(submitEl) : null,
              complete,failed,override:false,rawMarks,viewport
            }});
          }}
          candidates.sort((a,b) => b.score-a.score);
          return candidates[0] || {{
            kind:'none',scope:'document',score:0,rows:0,columns:0,tileCount:0,
            instruction:'',sources:[],submitText:'',submitBounds:null,
            complete,failed,override:false,rawMarks:[],viewport
          }};
        }})()
        """
        try:
            value = self._evaluate(script)
        except Exception:
            return self._empty("document")
        return self._normalize(value, default_scope="document")

    def _snapshot_extended_frames(self) -> Dict[str, Any]:
        try:
            frames = list(self._sb.find_elements("iframe") or [])
        except Exception:
            return self._empty("iframe")

        viewport = self._top_level_viewport()
        best = self._empty("iframe")
        best_rank = float("-inf")
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
            if not (self.MIN_COUNT <= len(images) <= self.MAX_COUNT):
                continue
            local_rects = [self._element_position(image) for image in images]
            shape = self._infer_shape(local_rects)
            if shape is None:
                continue
            rows, columns = shape
            sources = [self._element_image_source(img) for img in images]
            raw_marks = []
            valid = True
            for index, image in enumerate(images):
                local = local_rects[index]
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
                alt = self._element_attribute(image, "alt")
                identity = (
                    self._element_attribute(image, "id")
                    or self._element_attribute(image, "data-testid")
                    or f"slot:{index}"
                )
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
            scope = f"iframe:{frame_index}"
            marks = build_stable_marks(raw_marks, scope=scope, viewport=viewport)
            candidate = {
                "kind": "image-grid",
                "scope": scope,
                "score": 82 if all(sources) else 70,
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
            if not self._candidate_is_plausible(candidate):
                continue
            rank = self._candidate_rank(candidate)
            if rank > best_rank:
                best = candidate
                best_rank = rank
        return best

    def _snapshot_oopif_frames(self) -> Dict[str, Any]:
        discover = getattr(self._sb, "ares_oopif_discover", None)
        evaluate = getattr(self._sb, "ares_oopif_evaluate", None)
        if not callable(discover) or not callable(evaluate):
            return self._empty("oopif")

        try:
            frames = list(discover() or [])
        except Exception:
            return self._empty("oopif")

        viewport = self._top_level_viewport()
        outcome: Dict[str, Any] | None = None
        best = self._empty("oopif")
        best_rank = float("-inf")
        for entry in frames:
            if not isinstance(entry, dict):
                continue
            path = [str(value) for value in entry.get("path") or [] if str(value)]
            if not path:
                continue
            try:
                evaluated = evaluate(path, _OOPIF_GRID_SCRIPT, [self._overrides])
            except Exception:
                continue
            if not isinstance(evaluated, dict):
                continue
            value = evaluated.get("value")
            if not isinstance(value, dict):
                continue
            try:
                offset_x = float(evaluated.get("offsetX") or 0.0)
                offset_y = float(evaluated.get("offsetY") or 0.0)
            except (TypeError, ValueError):
                offset_x = offset_y = 0.0

            scope = "oopif:" + "/".join(path)
            if value.get("kind") != "image-grid":
                if bool(value.get("complete")) or bool(value.get("failed")):
                    outcome = {
                        **self._empty(scope),
                        "complete": bool(value.get("complete")),
                        "failed": bool(value.get("failed")),
                        "viewport": viewport,
                    }
                continue

            raw_marks = []
            for raw in value.get("rawMarks") or []:
                if not isinstance(raw, dict):
                    continue
                item = dict(raw)
                bounds = dict(item.get("visualBounds") or {})
                try:
                    bounds["x"] = float(bounds.get("x") or 0.0) + offset_x
                    bounds["y"] = float(bounds.get("y") or 0.0) + offset_y
                except (TypeError, ValueError):
                    continue
                item["visualBounds"] = bounds
                raw_marks.append(item)

            marks = build_stable_marks(raw_marks, scope=scope, viewport=viewport)
            candidate = {
                "kind": "image-grid",
                "scope": scope,
                "score": int(value.get("score") or 0),
                "rows": int(value.get("rows") or 0),
                "columns": int(value.get("columns") or 0),
                "tileCount": int(value.get("tileCount") or 0),
                "instruction": str(value.get("instruction") or ""),
                "sources": [str(source) for source in value.get("sources") or []],
                "submitText": str(value.get("submitText") or ""),
                "complete": bool(value.get("complete")),
                "failed": bool(value.get("failed")),
                "override": False,
                "viewport": viewport,
                "marks": marks,
            }
            submit_bounds = value.get("submitBounds")
            if isinstance(submit_bounds, dict):
                try:
                    candidate["submitBounds"] = {
                        "x": float(submit_bounds.get("x") or 0.0) + offset_x,
                        "y": float(submit_bounds.get("y") or 0.0) + offset_y,
                        "width": float(submit_bounds.get("width") or 0.0),
                        "height": float(submit_bounds.get("height") or 0.0),
                    }
                except (TypeError, ValueError):
                    candidate["submitBounds"] = None
            else:
                candidate["submitBounds"] = None

            reference_bounds = value.get("referenceBounds")
            if isinstance(reference_bounds, dict):
                try:
                    candidate["referenceBounds"] = {
                        "x": float(reference_bounds.get("x") or 0.0) + offset_x,
                        "y": float(reference_bounds.get("y") or 0.0) + offset_y,
                        "width": float(reference_bounds.get("width") or 0.0),
                        "height": float(reference_bounds.get("height") or 0.0),
                    }
                except (TypeError, ValueError):
                    candidate["referenceBounds"] = None
            else:
                candidate["referenceBounds"] = None
            candidate["referenceSource"] = str(value.get("referenceSource") or "")

            if not self._candidate_is_plausible(candidate):
                continue
            rank = self._candidate_rank(candidate)
            if rank > best_rank:
                best = candidate
                best_rank = rank
        if best.get("kind") == "image-grid":
            return best
        return outcome if outcome is not None else best

    @classmethod
    def _infer_shape(cls, rects: List[Dict[str, Any] | None]) -> Tuple[int, int] | None:
        if not rects or any(not isinstance(rect, dict) for rect in rects):
            return None
        try:
            widths = [float(rect.get("width") or 0.0) for rect in rects if isinstance(rect, dict)]
            heights = [float(rect.get("height") or 0.0) for rect in rects if isinstance(rect, dict)]
            centers_x = [
                float(rect.get("x") or 0.0) + float(rect.get("width") or 0.0) / 2.0
                for rect in rects if isinstance(rect, dict)
            ]
            centers_y = [
                float(rect.get("y") or 0.0) + float(rect.get("height") or 0.0) / 2.0
                for rect in rects if isinstance(rect, dict)
            ]
        except (TypeError, ValueError):
            return None
        if not widths or min(widths) <= 0 or min(heights) <= 0:
            return None
        avg_w = sum(widths) / len(widths)
        avg_h = sum(heights) / len(heights)
        rows = cls._cluster_count(centers_y, max(6.0, min(36.0, avg_h * 0.45)))
        columns = cls._cluster_count(centers_x, max(6.0, min(36.0, avg_w * 0.45)))
        if not (cls.MIN_DIM <= rows <= cls.MAX_DIM and cls.MIN_DIM <= columns <= cls.MAX_DIM):
            return None
        return (rows, columns) if rows * columns == len(rects) else None

    @staticmethod
    def _cluster_count(values: List[float], tolerance: float) -> int:
        clusters: List[List[float]] = []
        for value in sorted(values):
            if not clusters or abs(value - (sum(clusters[-1]) / len(clusters[-1]))) > tolerance:
                clusters.append([value])
            else:
                clusters[-1].append(value)
        return len(clusters)
