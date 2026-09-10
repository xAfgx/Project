from __future__ import annotations

from typing import Any, Dict, Iterable, List

from scope_locked_grid_site_adapter import ScopeLockedGridSiteAdapter as _BaseScopeLockedGridSiteAdapter
from stable_marks import build_stable_marks


def _install_object_id_frame_resolution() -> None:
    """Use the stable RemoteObject route when resolving iframe owners.

    The manual profile browser imports FlatCdpTargetRegistry directly, so it does
    not automatically receive the task-worker monkey patch. Installing the same
    proven objectId-based resolver here keeps both runtime entry points aligned.
    """
    try:
        import task_browser_worker_oopif_impl as impl
    except Exception:
        return

    registry_type = getattr(impl, "FlatCdpTargetRegistry", None)
    if registry_type is None:
        return
    if bool(getattr(registry_type, "_ares_object_id_frame_resolution", False)):
        return

    def _child_frame_id(self: Any, frame_id: str, selector: str) -> str:
        selector_json = impl.json.dumps(str(selector), ensure_ascii=False)
        last: BaseException | None = None
        for attempt in range(3):
            route = self._wait_for_route(frame_id, timeout=1.5)
            session_id = str(route["sessionId"])
            try:
                response = self.call(
                    "Runtime.evaluate",
                    {
                        "expression": f"document.querySelector({selector_json})",
                        "contextId": int(route["contextId"]),
                        "returnByValue": False,
                        "awaitPromise": True,
                    },
                    session_id=session_id,
                )
                if response.get("exceptionDetails"):
                    raise RuntimeError(f"Runtime.evaluate failed: {response.get('exceptionDetails')}")
                remote = response.get("result") if isinstance(response.get("result"), dict) else {}
                object_id = str(remote.get("objectId") or "")
                if not object_id:
                    raise LookupError(f"Frame path no longer resolves at {selector}")
                try:
                    description = self.call(
                        "DOM.describeNode",
                        {"objectId": object_id, "depth": 0, "pierce": True},
                        session_id=session_id,
                    )
                    node = description.get("node") if isinstance(description.get("node"), dict) else {}
                    child_frame_id = str(node.get("frameId") or "")
                    if not child_frame_id:
                        raise LookupError(f"CDP frameId is unavailable at {selector}")
                    self._wait_for_route(child_frame_id, timeout=2.0)
                    return child_frame_id
                finally:
                    try:
                        self.call(
                            "Runtime.releaseObject",
                            {"objectId": object_id},
                            session_id=session_id,
                            timeout=1.0,
                        )
                    except Exception:
                        pass
            except BaseException as exc:
                last = exc
                text = str(exc).lower()
                stale = (
                    "could not find node" in text
                    or "cannot find context" in text
                    or "execution context was destroyed" in text
                    or "inspected target navigated or closed" in text
                    or "no frame with given id" in text
                )
                if not stale or attempt >= 2:
                    raise
                impl.time.sleep(0.03 * (attempt + 1))
        if last is not None:
            raise last
        raise RuntimeError("Frame owner resolution failed without an error")

    registry_type._child_frame_id = _child_frame_id
    registry_type._ares_object_id_frame_resolution = True


_install_object_id_frame_resolution()


RUNTIME_OOPIF_GRID_SCRIPT = r"""
return (() => {
  const viewport = {
    width: window.innerWidth || document.documentElement.clientWidth || 0,
    height: window.innerHeight || document.documentElement.clientHeight || 0,
    scrollX: window.scrollX || 0,
    scrollY: window.scrollY || 0,
    devicePixelRatio: window.devicePixelRatio || 1,
  };
  const debug = {
    scannedElements: 0,
    visualCount: 0,
    visualAncestorCandidates: 0,
    directChildCandidates: 0,
    acceptedCandidates: 0,
    rejections: {},
  };
  const reject = reason => {
    debug.rejections[reason] = Number(debug.rejections[reason] || 0) + 1;
  };
  const visible = el => {
    if (!el?.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width >= 20 && r.height >= 20
      && r.right > 0 && r.bottom > 0
      && r.left < viewport.width && r.top < viewport.height
      && s.display !== 'none'
      && s.visibility !== 'hidden'
      && Number(s.opacity || 1) > 0;
  };
  const text = el => (el?.innerText || el?.textContent || el?.getAttribute?.('aria-label') || '')
    .trim().replace(/\s+/g, ' ');
  const rectOf = el => {
    const r = el.getBoundingClientRect();
    return {x:r.x,y:r.y,width:r.width,height:r.height};
  };
  const bgUrl = el => {
    if (!el || !visible(el)) return '';
    const bg = getComputedStyle(el).backgroundImage || '';
    const match = bg.match(/url\(["']?(.*?)["']?\)/i);
    return match?.[1] || '';
  };
  const sourceOf = tile => {
    const img = tile?.matches?.('img') ? tile : tile?.querySelector?.('img');
    if (img) {
      return img.currentSrc
        || img.src
        || img.getAttribute?.('src')
        || img.getAttribute?.('data-src')
        || img.getAttribute?.('data-lazy-src')
        || '';
    }
    const canvas = tile?.matches?.('canvas') ? tile : tile?.querySelector?.('canvas');
    if (canvas) {
      try { return canvas.toDataURL?.('image/png') || ''; } catch (_) { return ''; }
    }
    return bgUrl(tile);
  };
  const selectorFor = el => {
    if (!el) return '';
    if (el.id) return '#' + CSS.escape(el.id);
    const testId = el.getAttribute?.('data-testid');
    return testId ? '[data-testid="' + CSS.escape(testId) + '"]' : '';
  };
  const tileFor = visual => {
    if (!visual) return visual;
    // Prefer real interaction semantics before broad class-name heuristics.
    // Sprite/image nodes often contain "image"/"tile" in their own class name;
    // accepting the visual node itself there would measure the shifted sprite
    // box instead of the clickable grid cell that owns it.
    const interactive = visual.closest?.(
      'button,[role="button"],[role="gridcell"],[tabindex],label,li'
    );
    if (interactive) return interactive;
    return visual.closest?.(
      '[class*="tile" i],[class*="cell" i],[class*="option" i],[class*="choice" i],'
        + '[class*="square" i],[class*="image" i]'
    ) || visual;
  };
  const visualsIn = root => {
    const direct = [...(root.querySelectorAll?.('img,canvas,svg,[role="img"]') || [])].filter(visible);
    const backgrounds = [...(root.querySelectorAll?.('*') || [])].filter(el => {
      if (!visible(el) || !bgUrl(el)) return false;
      const r = el.getBoundingClientRect();
      return r.width <= Math.max(1000, viewport.width * 0.9)
        && r.height <= Math.max(1000, viewport.height * 0.9);
    });
    return [...new Set([...direct, ...backgrounds])];
  };
  const clusterCount = (values, tolerance) => {
    const sorted = [...values].sort((a,b) => a-b);
    const groups = [];
    for (const value of sorted) {
      const last = groups[groups.length - 1];
      if (!last || Math.abs(value - last.mean) > tolerance) {
        groups.push({mean:value,count:1});
      } else {
        last.mean = (last.mean * last.count + value) / (last.count + 1);
        last.count += 1;
      }
    }
    return groups.length;
  };
  const shapeOf = tiles => {
    if (tiles.length < 4 || tiles.length > 64) {
      reject('count');
      return null;
    }
    const rects = tiles.map(el => el.getBoundingClientRect());
    const avgW = rects.reduce((a,r) => a+r.width,0) / rects.length;
    const avgH = rects.reduce((a,r) => a+r.height,0) / rects.length;
    if (avgW < 20 || avgH < 20) {
      reject('small');
      return null;
    }
    const rows = clusterCount(
      rects.map(r => r.top + r.height / 2),
      Math.max(6, Math.min(36, avgH * 0.45))
    );
    const columns = clusterCount(
      rects.map(r => r.left + r.width / 2),
      Math.max(6, Math.min(36, avgW * 0.45))
    );
    if (rows < 2 || columns < 2 || rows > 8 || columns > 8) {
      reject('dimensions');
      return null;
    }
    if (rows * columns !== tiles.length) {
      reject('shape-product');
      return null;
    }
    const regular = rects.filter(r =>
      Math.abs(r.width - avgW) <= Math.max(12, avgW * 0.30)
      && Math.abs(r.height - avgH) <= Math.max(12, avgH * 0.30)
    ).length;
    if (regular / tiles.length < 0.82) {
      reject('regularity');
      return null;
    }
    return {rows, columns, regular, avgW, avgH};
  };
  const actionRx = /(select|click|choose|mark|pick|tap|verify|verification|continue|confirm|wähl|waehl|auswähl|auswaehl|klick|anklick|markier|prüf|pruef|bestät|bestaet|weiter)/i;
  const instructionFor = parent => {
    const nearby = [
      parent?.previousElementSibling,
      parent?.parentElement?.previousElementSibling,
      ...[...(parent?.parentElement?.querySelectorAll?.(
        'h1,h2,h3,h4,p,[class*="instruction" i],[class*="prompt" i],[class*="question" i]'
      ) || [])],
      ...[...(document.querySelectorAll?.(
        'h1,h2,h3,h4,p,[class*="instruction" i],[class*="prompt" i],[class*="question" i]'
      ) || [])],
    ].filter(el => el && visible(el));
    return nearby.find(el => actionRx.test(text(el))) || nearby[0] || null;
  };
  const submitFor = (parent, tiles) => [...(parent?.parentElement?.querySelectorAll?.(
    'button[type="submit"],input[type="submit"],button,[role="button"]'
  ) || [])].find(el => visible(el) && !tiles.includes(el)) || null;
  const tileLike = el => Boolean(el?.matches?.(
    '[role="gridcell"],[class*="tile" i],[class*="cell" i],[class*="option" i],'
      + '[class*="choice" i],[class*="square" i],[class*="image" i]'
  ));

  const candidates = [];
  const seen = new Set();
  const pushCandidate = (parent, rawTiles, origin) => {
    const tiles = [...new Set(rawTiles)].filter(visible);
    const shape = shapeOf(tiles);
    if (!shape) return;
    const key = tiles.map(el => {
      const r = el.getBoundingClientRect();
      return `${Math.round(r.x)}:${Math.round(r.y)}:${Math.round(r.width)}:${Math.round(r.height)}`;
    }).join('|');
    if (seen.has(key)) return;
    seen.add(key);

    const sources = tiles.map(sourceOf);
    const sourceCount = sources.filter(Boolean).length;
    const instruction = text(instructionFor(parent)).slice(0, 600);
    const submitEl = submitFor(parent, tiles);
    const submitText = text(submitEl).slice(0, 120);
    const hasActionContext = actionRx.test(instruction + ' ' + submitText);
    const tileLikeCount = tiles.filter(tileLike).length;
    const geometryRatio = shape.regular / tiles.length;
    const strongGeometry = geometryRatio >= 0.90;
    const majorityTileLike = tileLikeCount >= Math.ceil(tiles.length * 0.5);

    // Screenshot crops are the canonical visual input later in the pipeline.
    // DOM sources are useful evidence, but are not required when the structure
    // and surrounding interaction context make the grid sufficiently strong.
    if (sourceCount === 0 && !hasActionContext && !majorityTileLike) {
      reject('weak-evidence');
      return;
    }
    if (sourceCount > 0
        && sourceCount < Math.ceil(tiles.length * 0.5)
        && !hasActionContext
        && !majorityTileLike
        && !strongGeometry) {
      reject('partial-sources');
      return;
    }

    const rawMarks = tiles.map((tile, index) => ({
      role:'grid-tile',
      visualBounds:rectOf(tile),
      confidence:sources[index] ? .94 : (strongGeometry ? .82 : .76),
      selector:selectorFor(tile),
      structuralKey:['grid-tile',tile.tagName||'',tile.id||'',tile.getAttribute?.('data-testid')||'',`slot:${index}`].join('|'),
      semanticSignature:['grid-tile',text(tile).slice(0,160),sources[index]].join('|'),
      source:sources[index],
      label:text(tile).slice(0,160),
      score:index,
    }));
    let score = 72;
    score += Math.round(14 * sourceCount / tiles.length);
    score += Math.round(10 * geometryRatio);
    if (hasActionContext) score += 8;
    if (submitEl) score += 3;
    if (origin === 'direct-children') score += 4;
    if (origin === 'visual-ancestor') score += 2;
    if (shape.avgW >= 64 && shape.avgH >= 64) score += 3;
    candidates.push({
      kind:'image-grid',
      scope:'oopif',
      origin,
      score,
      rows:shape.rows,
      columns:shape.columns,
      tileCount:tiles.length,
      instruction,
      sources,
      submitText,
      submitBounds:submitEl ? rectOf(submitEl) : null,
      complete:false,
      failed:false,
      override:false,
      rawMarks,
      viewport,
      debug:{origin,sourceCount,tileLikeCount,hasActionContext,strongGeometry},
    });
    debug.acceptedCandidates += 1;
  };

  // First recover image/canvas/background based grids even when the actual tile
  // nodes are nested below row/wrapper containers instead of being siblings.
  const visuals = visualsIn(document);
  debug.visualCount = visuals.length;
  const parents = new Set();
  for (const visual of visuals) {
    let node = tileFor(visual);
    for (let depth = 0; node && depth < 6; depth += 1, node = node.parentElement) {
      if (node.parentElement) parents.add(node.parentElement);
    }
  }
  debug.visualAncestorCandidates = parents.size;
  for (const parent of parents) {
    pushCandidate(parent, visualsIn(parent).map(tileFor), 'visual-ancestor');
  }

  // Keep the cheap generic direct-child path for plain DIV/canvas layouts.
  const allElements = [...(document.querySelectorAll?.('body *') || [])];
  debug.scannedElements = allElements.length;
  for (const parent of allElements.slice(0, 4000)) {
    if (!visible(parent)) continue;
    const children = [...parent.children].filter(visible);
    if (children.length < 4 || children.length > 64) continue;
    debug.directChildCandidates += 1;
    pushCandidate(parent, children, 'direct-children');
  }

  candidates.sort((a,b) => b.score - a.score);
  const best = candidates[0] || null;
  if (best) {
    best.debug = {...debug, ...best.debug};
    return best;
  }
  return {
    kind:'none',scope:'oopif',score:0,rows:0,columns:0,tileCount:0,
    instruction:'',sources:[],submitText:'',submitBounds:null,
    complete:false,failed:false,override:false,rawMarks:[],viewport,debug,
  };
})();
"""

# Backward-compatible name for probes and older callers.
_DIRECT_CHILDREN_OOPIF_GRID_SCRIPT = RUNTIME_OOPIF_GRID_SCRIPT


class ScopeLockedGridSiteAdapter(_BaseScopeLockedGridSiteAdapter):
    """Add broad structural OOPIF grid discovery to the proven scope-lock adapter."""

    def _snapshot_oopif_path(self, path: Iterable[str]) -> Dict[str, Any]:
        primary = super()._snapshot_oopif_path(path)
        if primary.get("kind") == "image-grid" or bool(primary.get("complete")) or bool(primary.get("failed")):
            return primary

        clean_path = [str(value) for value in path if str(value)]
        scope = "oopif:" + "/".join(clean_path)
        evaluate = getattr(self._sb, "ares_oopif_evaluate", None)
        if not clean_path or not callable(evaluate):
            return primary

        try:
            evaluated = evaluate(clean_path, RUNTIME_OOPIF_GRID_SCRIPT, [])
        except Exception as exc:
            return {**primary, "oopifDebug": {"fallback": "broad-runtime", "error": str(exc)[:500]}}
        if not isinstance(evaluated, dict) or not isinstance(evaluated.get("value"), dict):
            return {**primary, "oopifDebug": {"fallback": "broad-runtime", "reason": "invalid-evaluate-result"}}

        value = evaluated["value"]
        debug = value.get("debug") if isinstance(value.get("debug"), dict) else {}
        metadata = {
            "framePath": clean_path,
            "frameId": str(evaluated.get("frameId") or ""),
            "documentEpoch": int(evaluated.get("documentEpoch") or 0),
            "sessionGeneration": int(evaluated.get("sessionGeneration") or 0),
            "oopifDebug": {"fallback": "broad-runtime", **debug},
        }
        if value.get("kind") != "image-grid":
            return {**primary, **metadata}

        try:
            offset_x = float(evaluated.get("offsetX") or 0.0)
            offset_y = float(evaluated.get("offsetY") or 0.0)
        except (TypeError, ValueError):
            offset_x = offset_y = 0.0

        viewport = self._top_level_viewport()
        raw_marks: List[Dict[str, Any]] = []
        for raw in value.get("rawMarks") or []:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            bounds = item.get("visualBounds")
            if isinstance(bounds, dict):
                adjusted = dict(bounds)
                try:
                    adjusted["x"] = float(adjusted.get("x") or 0.0) + offset_x
                    adjusted["y"] = float(adjusted.get("y") or 0.0) + offset_y
                except (TypeError, ValueError):
                    continue
                item["visualBounds"] = adjusted
            raw_marks.append(item)

        candidate: Dict[str, Any] = {
            "kind": "image-grid",
            "scope": scope,
            "origin": str(value.get("origin") or "broad-runtime"),
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
            "marks": build_stable_marks(raw_marks, scope=scope, viewport=viewport),
            **metadata,
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

        return candidate if self._candidate_is_plausible(candidate) else {**primary, **metadata}
