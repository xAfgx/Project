from __future__ import annotations

import base64
import json
import shutil
import socketserver
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse

from manual_profile_probe import WorkerClient


TARGETS = {
    "A": {0, 2, 4, 6, 8},
    "B": {1, 3, 5, 7},
}


class Recorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: List[Dict[str, Any]] = []

    def record(self, kind: str, variant: str, index: int | None = None) -> None:
        with self._lock:
            self._events.append({
                "kind": str(kind),
                "variant": str(variant),
                "index": index,
                "at": time.time(),
            })

    def events(self, variant: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._events if item.get("variant") == variant]


class SmokeHandler(BaseHTTPRequestHandler):
    recorder: Recorder

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        variant = (params.get("variant") or [""])[0]

        if parsed.path in {"/checkbox", "/clicked", "/mutated", "/submitted", "/success", "/failure"}:
            index: int | None = None
            if parsed.path == "/clicked":
                try:
                    index = int((params.get("index") or ["-1"])[0])
                except ValueError:
                    index = -1
            self.recorder.record(parsed.path.lstrip("/"), variant, index)
            self._send(b"ok", "text/plain; charset=utf-8")
            return

        if parsed.path.startswith("/frame/"):
            frame_variant = parsed.path.rsplit("/", 1)[-1]
            self._send(self._frame_html(frame_variant).encode("utf-8"), "text/html; charset=utf-8")
            return

        if parsed.path.startswith("/case/"):
            case_variant = parsed.path.rsplit("/", 1)[-1]
            self._send(self._case_html(case_variant).encode("utf-8"), "text/html; charset=utf-8")
            return

        self.send_response(404)
        self.end_headers()

    def _send(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _case_html(self, variant: str) -> str:
        port = int(self.server.server_address[1])
        frame_url = f"/frame/{variant}"
        if variant == "B":
            frame_url = f"http://localhost:{port}/frame/{variant}"
        return f"""<!doctype html>
<html><head><meta charset='utf-8'><title>ARES Visual Grid State Proof {variant}</title></head>
<body style='margin:0;background:#111;color:#fff;font-family:Arial'>
  <label id='launch-label' style='display:block;margin:24px'>
    <input id='launch-checkbox' type='checkbox'> Start visual test
  </label>
  <div id='frame-mount' style='position:relative;margin-left:137px;margin-top:91px;width:760px;height:720px'></div>
<script>
const checkbox = document.getElementById('launch-checkbox');
checkbox.addEventListener('change', () => {{
  if (!checkbox.checked || document.getElementById('visual-frame')) return;
  fetch('/checkbox?variant={variant}');
  const frame = document.createElement('iframe');
  frame.id = 'visual-frame';
  frame.title = 'Neutral university visual selection test';
  frame.src = {json.dumps(frame_url)};
  frame.style.cssText = 'border:0;width:760px;height:720px;display:block';
  document.getElementById('frame-mount').appendChild(frame);
}});
setTimeout(() => checkbox.click(), 250);
</script>
</body></html>"""

    @staticmethod
    def _svg_data(index: int, variant: str, *, mutation: bool = False) -> str:
        is_target = index in TARGETS[variant]
        marker = "<circle cx='7' cy='7' r='2' fill='#fff' opacity='0.02'/>" if mutation else ""
        if variant == "A":
            if is_target:
                shape = "<rect x='28' y='28' width='124' height='124' rx='8' fill='#e11d48'/>"
                label = "RED SQUARE"
            else:
                shape = "<circle cx='90' cy='90' r='60' fill='#2563eb'/>"
                label = "BLUE CIRCLE"
        else:
            if is_target:
                shape = "<circle cx='90' cy='90' r='60' fill='#facc15'/>"
                label = "YELLOW CIRCLE"
            else:
                shape = "<polygon points='90,20 160,150 20,150' fill='#475569'/>"
                label = "GRAY TRIANGLE"

        svg = f"""<svg xmlns='http://www.w3.org/2000/svg' width='180' height='180'>
<rect width='180' height='180' fill='white'/>{shape}{marker}
<text x='90' y='171' text-anchor='middle' font-family='Arial' font-size='14' font-weight='700' fill='black'>{label}</text>
</svg>"""
        return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")

    @classmethod
    def _frame_html(cls, variant: str) -> str:
        tiles: List[str] = []
        mutated_sources: Dict[int, str] = {}
        for index in range(9):
            src = cls._svg_data(index, variant)
            mutated_sources[index] = cls._svg_data(index, variant, mutation=True)
            tiles.append(
                f"<button class='tile' data-index='{index}' onclick='tileClicked({index})' "
                "style='padding:0;border:2px solid #fff;background:#000;width:180px;height:180px'>"
                f"<img id='tile-image-{index}' src='{src}' alt='tile-{index}' "
                "style='display:block;width:176px;height:176px;object-fit:cover'></button>"
            )
        prompt = "Select all images with red squares" if variant == "A" else "Select all images with yellow circles"
        expected = sorted(TARGETS[variant])
        grid = "".join(tiles)
        mutation_sources = json.dumps({str(k): v for k, v in mutated_sources.items()})
        return f"""<!doctype html>
<html><head><meta charset='utf-8'><style>
body{{margin:0;background:#222;color:#fff;font-family:Arial;text-align:center}}
#grid{{display:grid;grid-template-columns:repeat(3,180px);gap:8px;justify-content:center;margin:18px auto}}
.tile.selected{{outline:4px solid white;outline-offset:-6px}}
#submit{{width:220px;height:48px;font-size:18px}}
#success,#failure{{display:none;font-size:28px;font-weight:700;margin-top:120px}}
</style></head><body>
<div id='mount'><div id='waiting' style='padding:80px'>Preparing neutral visual task...</div></div>
<div id='success'>VISUAL TEST COMPLETE</div>
<div id='failure'>VISUAL TEST FAILED</div>
<script>
const expected = {json.dumps(expected)};
const mutationSources = {mutation_sources};
const selected = new Set();
let mutationDone = false;
function exactSelection() {{
  const actual = [...selected].sort((a,b) => a-b);
  return actual.length === expected.length && actual.every((value,index) => value === expected[index]);
}}
function tileClicked(index) {{
  selected.add(index);
  document.querySelector(`[data-index="${{index}}"]`)?.classList.add('selected');
  fetch(`/clicked?variant={variant}&index=${{index}}`);
  if ({str(variant == "B").lower()} && !mutationDone && expected.includes(index)) {{
    const replacement = expected.find(value => value !== index && !selected.has(value));
    if (replacement !== undefined) {{
      mutationDone = true;
      const image = document.getElementById(`tile-image-${{replacement}}`);
      if (image) image.src = mutationSources[String(replacement)];
      fetch('/mutated?variant={variant}');
    }}
  }}
}}
function submitSelection() {{
  fetch('/submitted?variant={variant}');
  if (exactSelection()) {{
    document.getElementById('grid').style.display = 'none';
    document.getElementById('submit').style.display = 'none';
    document.querySelector('.instruction').style.display = 'none';
    document.getElementById('success').style.display = 'block';
    fetch('/success?variant={variant}');
  }} else {{
    document.getElementById('failure').style.display = 'block';
    fetch('/failure?variant={variant}');
  }}
}}
setTimeout(() => {{
  document.getElementById('mount').innerHTML = `<h2 class='instruction'>{prompt}</h2><div id='grid'>{grid}</div><button id='submit' type='button' onclick='submitSelection()'>Confirm selection</button>`;
}}, 2800);
</script>
</body></html>"""

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def _start_server() -> tuple[socketserver.TCPServer, Recorder, str]:
    recorder = Recorder()

    class Handler(SmokeHandler):
        pass

    Handler.recorder = recorder
    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    return server, recorder, f"http://{host}:{port}"


def _runtime_state(client: WorkerClient, request_id: str) -> Dict[str, Any]:
    client.send({"type": "auto-interaction-state", "requestId": request_id})
    message = client.wait("auto-interaction-state", request_id, 6)
    return dict(message.get("state") or {})


def _wait_for_parent_iframe_baseline(client: WorkerClient, variant: str, timeout: float = 2.0) -> str:
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        state = _runtime_state(client, f"baseline-{variant}-{attempt}")
        watchdog = state.get("watchdog") if isinstance(state.get("watchdog"), dict) else {}
        last = watchdog.get("last") if isinstance(watchdog.get("last"), dict) else {}
        if int(last.get("iframes") or 0) >= 1:
            fingerprint = str(last.get("actionFingerprint") or "")
            if fingerprint:
                return fingerprint
        time.sleep(0.15)
    raise AssertionError(f"Variant {variant}: parent iframe baseline was not observed before delayed grid load")


def _wait_for_grid(client: WorkerClient, variant: str, timeout: float = 25.0) -> Dict[str, Any]:
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        request_id = f"grid-{variant}-{attempt}"
        client.send({"type": "site-grid-state", "requestId": request_id})
        message = client.wait("site-grid-state", request_id, 6)
        state = message.get("state") or {}
        if state.get("kind") == "image-grid" and int(state.get("tileCount") or 0) == 9:
            return dict(state)
        time.sleep(0.35)
    raise AssertionError(f"Variant {variant}: delayed 3x3 grid was not detected")


def _assert_top_level_geometry(state: Dict[str, Any], variant: str) -> None:
    marks = [
        mark for mark in state.get("marks") or []
        if isinstance(mark, dict) and mark.get("role") == "grid-tile"
    ]
    if len(marks) != 9:
        raise AssertionError(f"Variant {variant}: expected 9 stable marks, got {len(marks)}")
    xs = [float((mark.get("visualBounds") or {}).get("x") or 0.0) for mark in marks]
    ys = [float((mark.get("visualBounds") or {}).get("y") or 0.0) for mark in marks]
    if min(xs) < 130 or min(ys) < 85:
        raise AssertionError(f"Variant {variant}: frame offset was not applied to top-level bounds: min=({min(xs)}, {min(ys)})")
    viewport = state.get("viewport") if isinstance(state.get("viewport"), dict) else {}
    if float(viewport.get("width") or 0.0) <= 0 or float(viewport.get("height") or 0.0) <= 0:
        raise AssertionError(f"Variant {variant}: top-level viewport is missing: {viewport}")


def _trace_entries(path: Path) -> List[Dict[str, Any]]:
    if not path.exists() or path.stat().st_size <= 0:
        return []
    result: List[Dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            result.append(entry)
    return result


def _wait_for_complete_result(
    profile_dir: Path,
    recorder: Recorder,
    variant: str,
    *,
    require_stale_retry: bool,
    timeout: float = 55.0,
) -> None:
    trace = profile_dir / ".ares-visual-trace.jsonl"
    observations = profile_dir / ".ares-observations"
    expected = TARGETS[variant]
    deadline = time.time() + timeout

    while time.time() < deadline:
        entries = _trace_entries(trace)
        decisions: List[Dict[str, Any]] = []
        screenshot_results: List[Dict[str, Any]] = []
        stale_retry_seen = False
        for entry in entries:
            payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
            if entry.get("phase") == "decision":
                decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
                if decision.get("model") == "google/siglip2-base-patch16-224":
                    decisions.append(decision)
            if entry.get("phase") == "grid-screenshot-result":
                result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
                screenshot_results.append(result)
                if str(result.get("reason") or "") == "stale-grid-during-selection":
                    stale_retry_seen = True
            if entry.get("phase") == "action":
                action_payload = payload.get("result") if isinstance(payload.get("result"), dict) else {}
                if str(action_payload.get("reason") or "") == "stale-grid-during-selection":
                    stale_retry_seen = True

        exact_decision = any(
            {int(value) for value in decision.get("selectedIndexes") or []} == expected
            for decision in decisions
        )
        final_result = next(
            (
                result for result in reversed(screenshot_results)
                if result.get("verified") is True
                and str((result.get("verification") or {}).get("reason") or "") == "explicit-complete"
            ),
            None,
        )
        events = recorder.events(variant)
        clicked = {
            int(item.get("index"))
            for item in events
            if item.get("kind") == "clicked" and isinstance(item.get("index"), int) and int(item.get("index")) >= 0
        }
        checkbox_seen = any(item.get("kind") == "checkbox" for item in events)
        submit_seen = any(item.get("kind") == "submitted" for item in events)
        success_seen = any(item.get("kind") == "success" for item in events)
        failure_seen = any(item.get("kind") == "failure" for item in events)
        mutation_seen = any(item.get("kind") == "mutated" for item in events)

        if final_result is not None and exact_decision and checkbox_seen and submit_seen and success_seen:
            invariants = final_result.get("invariants") if isinstance(final_result.get("invariants"), dict) else {}
            screenshot = final_result.get("screenshot") if isinstance(final_result.get("screenshot"), dict) else {}
            action_result = final_result.get("result") if isinstance(final_result.get("result"), dict) else {}
            crop_boxes = screenshot.get("cropBoxes") or []
            clicked_mark_ids = [str(value) for value in action_result.get("clickedMarkIds") or []]

            if clicked != expected:
                raise AssertionError(f"Variant {variant}: browser click set mismatch: clicked={sorted(clicked)} expected={sorted(expected)}")
            if failure_seen:
                raise AssertionError(f"Variant {variant}: explicit failure state was reached")
            if int(invariants.get("tileCount") or 0) != 9 or int(invariants.get("markCount") or 0) != 9:
                raise AssertionError(f"Variant {variant}: 9-mark invariant failed: {invariants}")
            if int(invariants.get("cropCount") or 0) != 9 or int(invariants.get("readable") or 0) != 9:
                raise AssertionError(f"Variant {variant}: 9-crop invariant failed: {invariants}")
            if not bool(invariants.get("geometryReady")) or not bool(invariants.get("cropsReady")):
                raise AssertionError(f"Variant {variant}: crop geometry/readability invariant failed: {invariants}")
            if not bool(invariants.get("sameVisualState")):
                raise AssertionError(f"Variant {variant}: screenshot and classified state diverged: {invariants}")
            if len(crop_boxes) != 9:
                raise AssertionError(f"Variant {variant}: expected 9 crop boxes, got {crop_boxes}")
            if min(int(box[0]) for box in crop_boxes if isinstance(box, list) and len(box) == 4) < 100:
                raise AssertionError(f"Variant {variant}: screenshot crops lost frame X offset: {crop_boxes}")
            if len(clicked_mark_ids) != len(expected):
                raise AssertionError(f"Variant {variant}: not every selected stable mark was clicked: {action_result}")
            if action_result.get("submitted") is not True:
                raise AssertionError(f"Variant {variant}: submit was not confirmed by executor: {action_result}")
            if require_stale_retry and (not mutation_seen or not stale_retry_seen):
                raise AssertionError(
                    f"Variant {variant}: expected image mutation + stale-grid retry was not observed; "
                    f"mutation={mutation_seen} staleRetry={stale_retry_seen}"
                )
            screenshots = list(observations.glob("*.png")) if observations.exists() else []
            if not screenshots or not all(path.stat().st_size > 0 for path in screenshots):
                raise AssertionError(f"Variant {variant}: no valid observation screenshots were archived")
            return

        time.sleep(0.35)

    trace_tail = trace.read_text(encoding="utf-8")[-10000:] if trace.exists() else "<missing trace>"
    raise AssertionError(
        f"Variant {variant}: full checkbox -> delayed iframe -> 9 crops -> exact SigLIP2 -> all clicks -> "
        f"submit -> explicit success proof did not complete. events={recorder.events(variant)} trace_tail={trace_tail}"
    )


def _run_variant(base_url: str, recorder: Recorder, root: Path, variant: str) -> None:
    profile_dir = root / f"profile-{variant}" / ".ares-seleniumbase-cdp"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / ".ares-site-adapter.json").write_text(
        json.dumps(
            {
                "root": "#grid",
                "tiles": ".tile",
                "instruction": ".instruction",
                "submit": "#submit",
                "complete": "#success",
                "failed": "#failure",
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    client = WorkerClient(profile_dir, f"{base_url}/case/{variant}")
    try:
        parent_baseline = _wait_for_parent_iframe_baseline(client, variant)
        state = _wait_for_grid(client, variant)
        if int(state.get("rows") or 0) != 3 or int(state.get("columns") or 0) != 3:
            raise AssertionError(f"Variant {variant}: detected grid is not 3x3: {state}")
        _assert_top_level_geometry(state, variant)

        post_grid_runtime = _runtime_state(client, f"post-grid-{variant}")
        watchdog = post_grid_runtime.get("watchdog") if isinstance(post_grid_runtime.get("watchdog"), dict) else {}
        last = watchdog.get("last") if isinstance(watchdog.get("last"), dict) else {}
        post_grid_fingerprint = str(last.get("actionFingerprint") or "")
        if parent_baseline != post_grid_fingerprint:
            raise AssertionError(
                f"Variant {variant}: top-level watchdog changed while only existing iframe content loaded: "
                f"before={parent_baseline} after={post_grid_fingerprint}"
            )

        _wait_for_complete_result(
            profile_dir,
            recorder,
            variant,
            require_stale_retry=(variant == "B"),
        )
    finally:
        client.close()


def main() -> int:
    temporary = Path(tempfile.mkdtemp(prefix="ares-grid-runtime-smoke-"))
    server, recorder, base_url = _start_server()
    try:
        _run_variant(base_url, recorder, temporary, "A")
        _run_variant(base_url, recorder, temporary, "B")
        print(
            "ARES visual-grid proof passed: checkbox -> delayed existing iframe -> top-level offsets -> "
            "9 screenshot crops -> exact real SigLIP2 selection -> refreshed stable-mark clicks -> submit -> explicit success; "
            "variant B also proved image-change stale-state recovery."
        )
        return 0
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
