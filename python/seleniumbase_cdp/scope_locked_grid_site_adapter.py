from __future__ import annotations

import hashlib
from typing import Any, Dict, Iterable, List, Tuple

from extended_grid_site_adapter import ExtendedGridSiteAdapter, _OOPIF_GRID_SCRIPT
from stable_marks import build_stable_marks, stable_mark_digest


_GENERIC_FRAME_GRID_SCRIPT = r"""
return (() => {
  const viewport = {
    width: window.innerWidth || document.documentElement.clientWidth || 0,
    height: window.innerHeight || document.documentElement.clientHeight || 0,
    scrollX: window.scrollX || 0,
    scrollY: window.scrollY || 0,
    devicePixelRatio: window.devicePixelRatio || 1,
  };
  const visible = el => {
    if (!el?.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width >= 20 && r.height >= 20
      && r.right > 0 && r.bottom > 0
      && r.left < viewport.width && r.top < viewport.height
      && s.display !== 'none' && s.visibility !== 'hidden'
      && Number(s.opacity || 1) > 0;
  };
  const text = el => (el?.innerText || el?.textContent || el?.getAttribute?.('aria-label') || '')
    .trim().replace(/\s+/g, ' ');
  const bgUrl = el => {
    if (!visible(el)) return '';
    const bg = getComputedStyle(el).backgroundImage || '';
    const m = bg.match(/url\(["']?(.*?)["']?\)/i);
    return m?.[1] || '';
  };
  const sourceOf = el => {
    const img = el?.matches?.('img') ? el : el?.querySelector?.('img');
    if (img) return img.currentSrc || img.src || img.getAttribute('src') || img.getAttribute('data-src') || '';
    const canvas = el?.matches?.('canvas') ? el : el?.querySelector?.('canvas');
    if (canvas) {
      try { return canvas.toDataURL?.('image/png') || ''; } catch (_) { return ''; }
    }
    return bgUrl(el);
  };
  const rectOf = el => {
    const r = el.getBoundingClientRect();
    return {x:r.x,y:r.y,width:r.width,height:r.height};
  };
  const tileFor = visual => {
    if (visual?.matches?.('img') && /rc-image-tile|rc-imageselect/i.test(String(visual.className || ''))) {
      const cell = visual.closest?.('td.rc-imageselect-tile,[class*="rc-imageselect-tile" i]');
      if (cell) return cell;
    }
    return visual.closest?.(
      'button,[role="button"],[tabindex],label,li,[class*="tile" i],[class*="cell" i],[class*="option" i],[class*="choice" i]'
    ) || visual;
  };
  const isControl = el => /^recaptcha-/i.test(String(el?.id || el?.getAttribute?.('id') || ''));
  const visualsIn = root => {
    const direct = [...(root.querySelectorAll?.('img,canvas') || [])].filter(visible).filter(el => !isControl(el));
    const backgrounds = [...(root.querySelectorAll?.('*') || [])].filter(el => {
      if (!visible(el) || !bgUrl(el) || isControl(el)) return false;
      const r = el.getBoundingClientRect();
      return r.width <= Math.max(900, viewport.width * 0.8)
        && r.height <= Math.max(900, viewport.height * 0.8);
    });
    return [...new Set([...direct, ...backgrounds])];
  };
  const clusterCount = (values, tolerance) => {
    const sorted = [...values].sort((a,b) => a-b);
    const groups = [];
    for (const value of sorted) {
      const last = groups[groups.length - 1];
      if (!last || Math.abs(value - last.mean) > tolerance) groups.push({mean:value,count:1});
      else {
        last.mean = (last.mean * last.count + value) / (last.count + 1);
        last.count += 1;
      }
    }
    return groups.length;
  };
  const shapeOf = tiles => {
    const rects = tiles.map(el => el.getBoundingClientRect());
    if (rects.length < 4 || rects.length > 64) return null;
    const avgW = rects.reduce((a,r) => a+r.width,0) / rects.length;
    const avgH = rects.reduce((a,r) => a+r.height,0) / rects.length;
    const rows = clusterCount(rects.map(r => r.top+r.height/2), Math.max(6, Math.min(36, avgH*.45)));
    const cols = clusterCount(rects.map(r => r.left+r.width/2), Math.max(6, Math.min(36, avgW*.45)));
    if (rows < 2 || cols < 2 || rows > 8 || cols > 8 || rows*cols !== tiles.length) return null;
    const regular = rects.filter(r =>
      Math.abs(r.width-avgW) <= Math.max(12,avgW*.3)
      && Math.abs(r.height-avgH) <= Math.max(12,avgH*.3)
    ).length;
    if (regular / rects.length < .82) return null;
    return {rows,cols,regular,avgW,avgH};
  };
  const actionRx = /(select|click|choose|mark|verify|continue|confirm|wähl|waehl|klick|markier|prüf|pruef|bestät|bestaet|weiter)/i;
  const instructionFor = parent => {
    const nearby = [
      parent?.previousElementSibling,
      parent?.parentElement?.previousElementSibling,
      ...[...(parent?.parentElement?.querySelectorAll?.('h1,h2,h3,h4,p,[class*="instruction" i],[class*="prompt" i],[class*="question" i]') || [])],
      ...[...(document.querySelectorAll?.('h1,h2,h3,h4,p,[class*="instruction" i],[class*="prompt" i],[class*="question" i]') || [])],
    ].filter(el => el && visible(el));
    return nearby.find(el => actionRx.test(text(el))) || nearby[0] || null;
  };
  const parents = new Set();
  for (const visual of visualsIn(document)) {
    let node = tileFor(visual);
    for (let depth=0; node && depth<5; depth++, node=node.parentElement) {
      if (node.parentElement) parents.add(node.parentElement);
    }
  }
  const candidates = [];
  for (const parent of parents) {
    const visuals = visualsIn(parent);
    const tiles = [...new Set(visuals.map(tileFor))].filter(visible);
    const shape = shapeOf(tiles);
    if (!shape) continue;
    const sources = tiles.map(sourceOf);
    const sourceCount = sources.filter(Boolean).length;
    if (sourceCount < Math.ceil(tiles.length * .5)) continue;
    const instructionEl = instructionFor(parent);
    const instruction = text(instructionEl).slice(0,600);
    const submitEl = [...(parent.parentElement?.querySelectorAll?.('button[type="submit"],input[type="submit"],button,[role="button"]') || [])]
      .find(el => visible(el) && !tiles.includes(el)) || null;
    const submitText = text(submitEl).slice(0,120);
    const marks = tiles.map((tile,index) => ({
      role:'grid-tile',
      visualBounds:rectOf(tile),
      confidence:sources[index] ? .92 : .72,
      selector: tile.id ? '#' + CSS.escape(tile.id) : '',
      structuralKey:['grid-tile',tile.tagName||'',tile.id||'',tile.getAttribute?.('data-testid')||'',`slot:${index}`].join('|'),
      semanticSignature:['grid-tile',text(tile).slice(0,160),sources[index]].join('|'),
      source:sources[index],
      label:text(tile).slice(0,160),
      score:index,
    }));
    let score = 74 + Math.round(14*sourceCount/tiles.length) + Math.round(8*shape.regular/tiles.length);
    if (actionRx.test(instruction + ' ' + submitText)) score += 8;
    if (submitEl) score += 3;
    candidates.push({
      kind:'image-grid',scope:'oopif',score,rows:shape.rows,columns:shape.cols,tileCount:tiles.length,
      instruction,sources,submitText,submitBounds:submitEl ? rectOf(submitEl) : null,
      complete:false,failed:false,override:false,rawMarks:marks,viewport,
    });
  }
  candidates.sort((a,b)=>b.score-a.score);
  return candidates[0] || {
    kind:'none',scope:'oopif',score:0,rows:0,columns:0,tileCount:0,instruction:'',sources:[],
    submitText:'',submitBounds:null,complete:false,failed:false,override:false,rawMarks:[],viewport
  };
})();
"""


class ScopeLockedGridSiteAdapter(ExtendedGridSiteAdapter):
    """Global grid discovery followed by cheap scope-local revalidation."""

    def __init__(self, seleniumbase_cdp: Any, *, overrides: Dict[str, str] | None = None) -> None:
        super().__init__(seleniumbase_cdp, overrides=overrides)
        self._scope_lock: Dict[str, Any] | None = None

    def poll(self) -> Dict[str, Any]:
        if self._scope_lock is not None:
            local = self._revalidate_locked_scope()
            if self._terminal(local):
                self._scope_lock = None
                return self._with_generation(local)
            if self._matches_lock(local):
                return self._with_generation({**local, "scopeLocked": True})
            self._scope_lock = None

        discovered = self._discover_global()
        if discovered.get("kind") != "image-grid" or not self._candidate_is_plausible(discovered):
            return discovered

        primed = self._prime_scope(discovered)
        candidate = primed if primed is not None and self._same_grid_identity(discovered, primed) else discovered
        lock = self._build_lock(candidate)
        if lock is not None:
            self._scope_lock = lock
            if primed is not None:
                return self._with_generation({**candidate, "scopeLocked": True})
            return {**candidate, "scopeLocked": True}
        return candidate

    def _discover_global(self) -> Dict[str, Any]:
        producers = (
            self._snapshot_document,
            self._snapshot_extended_document,
            self._snapshot_nested_frames,
            self._snapshot_extended_frames,
            self._snapshot_oopif_frames,
        )
        debug: List[Dict[str, Any]] = []
        outcome: Dict[str, Any] | None = None
        rejected: Dict[str, Any] | None = None
        best: Dict[str, Any] | None = None
        best_rank = float("-inf")

        try:
            frames = list(self._sb.find_elements("iframe") or [])
            debug.append({"producer": "iframe-enumeration", "count": len(frames)})
        except Exception as exc:
            debug.append({"producer": "iframe-enumeration", "error": str(exc)[:500]})

        for producer in producers:
            name = getattr(producer, "__name__", producer.__class__.__name__)
            try:
                snapshot = producer()
            except Exception as exc:
                debug.append({"producer": name, "error": str(exc)[:500]})
                continue
            debug.append({
                "producer": name,
                "kind": str(snapshot.get("kind") or "none"),
                "scope": str(snapshot.get("scope") or ""),
                "score": int(snapshot.get("score") or 0),
                "tileCount": int(snapshot.get("tileCount") or 0),
                "instruction": str(snapshot.get("instruction") or "")[:240],
                "framePath": [str(value) for value in snapshot.get("framePath") or [] if str(value)],
            })
            if snapshot.get("kind") == "none":
                if self._terminal(snapshot):
                    outcome = snapshot
                continue
            if not self._candidate_is_plausible(snapshot):
                rejected = snapshot
                continue
            rank = self._candidate_rank(snapshot)
            if best is None or rank > best_rank:
                best = snapshot
                best_rank = rank

        discover = getattr(self._sb, "ares_oopif_discover", None)
        if callable(discover):
            try:
                entries = [entry for entry in (discover() or []) if isinstance(entry, dict)]
                debug.append({
                    "producer": "ares_oopif_discover",
                    "count": len(entries),
                    "paths": [[str(value) for value in entry.get("path") or [] if str(value)] for entry in entries[:16]],
                })
            except Exception as exc:
                debug.append({"producer": "ares_oopif_discover", "error": str(exc)[:500]})
        else:
            debug.append({"producer": "ares_oopif_discover", "available": False})

        if outcome is not None:
            return self._with_generation({**outcome, "discoveryDebug": debug})
        if best is not None:
            return self._with_generation({**best, "discoveryDebug": debug})
        scope = str((rejected or {}).get("scope") or "document")
        return self._with_generation({**self._empty(scope), "discoveryDebug": debug})

    def _snapshot_oopif_frames(self) -> Dict[str, Any]:
        discover = getattr(self._sb, "ares_oopif_discover", None)
        if not callable(discover):
            return self._empty("oopif")
        try:
            entries = [entry for entry in (discover() or []) if isinstance(entry, dict)]
        except Exception:
            return self._empty("oopif")
        best = self._empty("oopif")
        best_rank = float("-inf")
        for entry in entries:
            path = [str(value) for value in entry.get("path") or [] if str(value)]
            if not path:
                continue
            candidate = self._snapshot_oopif_path(path)
            if candidate.get("kind") != "image-grid" or not self._candidate_is_plausible(candidate):
                continue
            rank = self._candidate_rank(candidate)
            if rank > best_rank:
                best = candidate
                best_rank = rank
        return best

    @staticmethod
    def _terminal(state: Dict[str, Any]) -> bool:
        return bool(state.get("complete")) or bool(state.get("failed"))

    def _prime_scope(self, discovered: Dict[str, Any]) -> Dict[str, Any] | None:
        scope = str(discovered.get("scope") or "")
        if scope.startswith("oopif:"):
            path = self._resolve_oopif_path(scope)
            if not path:
                return None
            return self._snapshot_oopif_path(path)
        if scope.startswith("iframe:"):
            try:
                frame_index = int(scope.split(":", 1)[1].split("/", 1)[0])
            except (TypeError, ValueError):
                return None
            return self._snapshot_frame_index(frame_index)
        if scope.startswith("document"):
            return self._snapshot_document_scope(discovered)
        return None

    def _revalidate_locked_scope(self) -> Dict[str, Any]:
        lock = self._scope_lock or {}
        scope = str(lock.get("scope") or "")
        if scope.startswith("oopif:"):
            path = [str(value) for value in lock.get("framePath") or [] if str(value)]
            return self._snapshot_oopif_path(path) if path else self._empty(scope)
        if scope.startswith("iframe:"):
            try:
                frame_index = int(lock.get("frameIndex"))
            except (TypeError, ValueError):
                return self._empty(scope)
            return self._snapshot_frame_index(frame_index)
        if scope.startswith("document"):
            return self._snapshot_document_scope(lock)
        return self._empty(scope or "document")

    def _snapshot_document_scope(self, expected: Dict[str, Any]) -> Dict[str, Any]:
        expected_scope = str(expected.get("scope") or "document")
        candidates = [self._snapshot_document(), self._snapshot_extended_document()]
        terminal: Dict[str, Any] | None = None
        best: Dict[str, Any] | None = None
        best_rank = float("-inf")
        for candidate in candidates:
            if self._terminal(candidate):
                terminal = candidate
            if candidate.get("kind") != "image-grid":
                continue
            if str(candidate.get("scope") or "") != expected_scope:
                continue
            if not self._candidate_is_plausible(candidate):
                continue
            rank = self._candidate_rank(candidate)
            if best is None or rank > best_rank:
                best = candidate
                best_rank = rank
        if best is not None:
            return best
        if terminal is not None:
            return terminal
        return self._empty(expected_scope)

    def _snapshot_frame_index(self, frame_index: int) -> Dict[str, Any]:
        scope = f"iframe:{frame_index}"
        try:
            frames = list(self._sb.find_elements("iframe") or [])
        except Exception:
            return self._empty(scope)
        if frame_index < 0 or frame_index >= len(frames):
            return self._empty(scope)

        frame = frames[frame_index]
        frame_position = self._element_position(frame)
        if not isinstance(frame_position, dict):
            return self._empty(scope)
        try:
            frame_x = float(frame_position.get("x") or 0.0)
            frame_y = float(frame_position.get("y") or 0.0)
            images = [img for img in (frame.query_selector_all("img") or []) if self._element_visible(img)]
        except Exception:
            return self._empty(scope)
        if not (self.MIN_COUNT <= len(images) <= self.MAX_COUNT):
            return self._empty(scope)

        rects = [self._element_position(image) for image in images]
        shape = self._infer_shape(rects)
        if shape is None:
            return self._empty(scope)
        rows, columns = shape
        sources = [self._element_image_source(image) for image in images]
        viewport = self._top_level_viewport()
        raw_marks: List[Dict[str, Any]] = []
        for index, image in enumerate(images):
            local = rects[index]
            if not isinstance(local, dict):
                return self._empty(scope)
            bounds = dict(local)
            try:
                bounds["x"] = float(bounds.get("x") or 0.0) + frame_x
                bounds["y"] = float(bounds.get("y") or 0.0) + frame_y
            except (TypeError, ValueError):
                return self._empty(scope)
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
            "marks": build_stable_marks(raw_marks, scope=scope, viewport=viewport),
        }
        return candidate if self._candidate_is_plausible(candidate) else self._empty(scope)

    def _resolve_oopif_path(self, scope: str) -> List[str]:
        discover = getattr(self._sb, "ares_oopif_discover", None)
        if not callable(discover):
            return []
        try:
            entries = list(discover() or [])
        except Exception:
            return []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            path = [str(value) for value in entry.get("path") or [] if str(value)]
            if path and "oopif:" + "/".join(path) == scope:
                return path
        return []

    def _snapshot_oopif_path(self, path: Iterable[str]) -> Dict[str, Any]:
        clean_path = [str(value) for value in path if str(value)]
        scope = "oopif:" + "/".join(clean_path)
        evaluate = getattr(self._sb, "ares_oopif_evaluate", None)
        if not clean_path or not callable(evaluate):
            return self._empty(scope or "oopif")
        try:
            evaluated = evaluate(clean_path, _OOPIF_GRID_SCRIPT, [self._overrides])
        except Exception:
            return self._empty(scope)
        if not isinstance(evaluated, dict):
            return self._empty(scope)
        value = evaluated.get("value")
        if not isinstance(value, dict):
            return self._empty(scope)
        if value.get("kind") != "image-grid":
            try:
                fallback = evaluate(clean_path, _GENERIC_FRAME_GRID_SCRIPT, [])
            except Exception:
                fallback = None
            if isinstance(fallback, dict) and isinstance(fallback.get("value"), dict):
                value = fallback["value"]
                evaluated = fallback

        metadata = {
            "framePath": clean_path,
            "frameId": str(evaluated.get("frameId") or ""),
            "documentEpoch": int(evaluated.get("documentEpoch") or 0),
            "sessionGeneration": int(evaluated.get("sessionGeneration") or 0),
        }
        if value.get("kind") != "image-grid":
            return {
                **self._empty(scope),
                **metadata,
                "complete": bool(value.get("complete")),
                "failed": bool(value.get("failed")),
            }

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

        return candidate if self._candidate_is_plausible(candidate) else self._empty(scope)

    @classmethod
    def _grid_identity(cls, state: Dict[str, Any]) -> Tuple[Any, ...]:
        marks = [mark for mark in state.get("marks") or [] if isinstance(mark, dict) and mark.get("role") == "grid-tile"]
        mark_identity = tuple(
            (str(mark.get("markId") or ""), str(mark.get("source") or mark.get("semanticVisualSignature") or ""))
            for mark in marks
        )
        return (
            str(state.get("scope") or ""),
            int(state.get("rows") or 0),
            int(state.get("columns") or 0),
            int(state.get("tileCount") or 0),
            mark_identity,
        )

    @classmethod
    def _same_grid_identity(cls, first: Dict[str, Any], second: Dict[str, Any]) -> bool:
        return first.get("kind") == "image-grid" and second.get("kind") == "image-grid" and cls._grid_identity(first) == cls._grid_identity(second)

    def _build_lock(self, state: Dict[str, Any]) -> Dict[str, Any] | None:
        if state.get("kind") != "image-grid":
            return None
        scope = str(state.get("scope") or "")
        lock: Dict[str, Any] = {"scope": scope, "identity": self._grid_identity(state)}
        if scope.startswith("iframe:"):
            try:
                lock["frameIndex"] = int(scope.split(":", 1)[1].split("/", 1)[0])
            except (TypeError, ValueError):
                return None
        if scope.startswith("oopif:"):
            path = [str(value) for value in state.get("framePath") or [] if str(value)]
            if not path:
                return None
            lock.update({
                "framePath": path,
                "frameId": str(state.get("frameId") or ""),
                "documentEpoch": int(state.get("documentEpoch") or 0),
                "sessionGeneration": int(state.get("sessionGeneration") or 0),
            })
        return lock

    def _matches_lock(self, state: Dict[str, Any]) -> bool:
        lock = self._scope_lock or {}
        if state.get("kind") != "image-grid":
            return False
        if self._grid_identity(state) != lock.get("identity"):
            return False
        if str(lock.get("scope") or "").startswith("oopif:"):
            if [str(value) for value in state.get("framePath") or [] if str(value)] != lock.get("framePath"):
                return False
            if str(state.get("frameId") or "") != str(lock.get("frameId") or ""):
                return False
            if int(state.get("documentEpoch") or 0) != int(lock.get("documentEpoch") or 0):
                return False
            if int(state.get("sessionGeneration") or 0) != int(lock.get("sessionGeneration") or 0):
                return False
        return True

    def _with_generation(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        signature_input = "|".join([
            str(snapshot.get("kind") or "none"),
            str(snapshot.get("scope") or ""),
            str(snapshot.get("tileCount") or 0),
            str(snapshot.get("instruction") or ""),
            str(bool(snapshot.get("complete"))),
            str(bool(snapshot.get("failed"))),
            stable_mark_digest(snapshot.get("marks") or []),
            str(snapshot.get("frameId") or ""),
            str(int(snapshot.get("documentEpoch") or 0)),
            str(int(snapshot.get("sessionGeneration") or 0)),
        ])
        signature = hashlib.sha256(signature_input.encode("utf-8", errors="ignore")).hexdigest()
        if signature != self._last_signature:
            self._generation += 1
            self._last_signature = signature
        return {**snapshot, "generation": self._generation, "signature": signature}
