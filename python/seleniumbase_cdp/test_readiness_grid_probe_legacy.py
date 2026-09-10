from __future__ import annotations

import base64
import json
import queue
import shutil
import socketserver
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

from extended_grid_site_adapter import ExtendedGridSiteAdapter
from manual_profile_probe import WorkerClient
from proximity_grid_action_executor import ProximityGridActionExecutor
from robust_vision_grid_classifier import RobustVisionGridClassifier


def _rects(rows: int, columns: int, *, size: float = 40.0, gap: float = 8.0):
    return [
        {
            "x": column * (size + gap),
            "y": row * (size + gap),
            "width": size,
            "height": size,
        }
        for row in range(rows)
        for column in range(columns)
    ]


def _marks(rows: int, columns: int):
    return [
        {
            "role": "grid-tile",
            "visualBounds": rect,
            "markId": f"tile-{index}",
        }
        for index, rect in enumerate(_rects(rows, columns))
    ]


def _tile_svg(index: int) -> str:
    shade = 48 + (index % 8) * 20
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='40' height='40'>"
        f"<rect width='40' height='40' fill='rgb({shade},120,180)'/>"
        f"<text x='20' y='25' text-anchor='middle' font-family='Arial' font-size='12' fill='white'>{index}</text>"
        "</svg>"
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


class Recorder:
    def __init__(self) -> None:
        self.hits: queue.Queue[Dict[str, str]] = queue.Queue()


class ProbeHandler(BaseHTTPRequestHandler):
    recorder: Recorder

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/clicked":
            params = parse_qs(parsed.query)
            self.recorder.hits.put({"type": "tile", "index": (params.get("index") or [""])[0]})
            self._send(b"ok", "text/plain; charset=utf-8")
            return
        if parsed.path == "/submitted":
            self.recorder.hits.put({"type": "submit", "index": ""})
            self._send(b"ok", "text/plain; charset=utf-8")
            return
        if parsed.path == "/":
            tiles = "".join(
                f"<button class='tile' data-index='{index}' aria-label='tile {index}' onclick=\"fetch('/clicked?index={index}')\">"
                f"<img src='{_tile_svg(index)}' alt='tile-{index}' draggable='false'>"
                "</button>"
                for index in range(64)
            )
            body = f"""<!doctype html>
<html><head><meta charset='utf-8'><style>
body{{font-family:Arial;margin:20px}}
.instruction{{margin:0 0 12px 0}}
#grid{{display:grid;grid-template-columns:repeat(8,40px);gap:8px;width:max-content}}
.tile{{width:40px;height:40px;padding:0;border:0;background:transparent}}
.tile img{{display:block;width:40px;height:40px}}
.submit{{margin-top:16px;padding:10px 18px}}
</style></head><body>
<h2 class='instruction'>Select the requested visual tiles</h2>
<div id='grid'>{tiles}</div>
<button class='submit' onclick=\"fetch('/submitted')\">OK</button>
</body></html>""".encode("utf-8")
            self._send(body, "text/html; charset=utf-8")
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

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def _start_server() -> tuple[socketserver.TCPServer, Recorder, str]:
    recorder = Recorder()

    class Handler(ProbeHandler):
        pass

    Handler.recorder = recorder
    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    return server, recorder, f"http://{host}:{port}/"


def _collect_hits(recorder: Recorder, expected_count: int, timeout: float = 10.0) -> list[Dict[str, str]]:
    hits: list[Dict[str, str]] = []
    deadline = time.time() + timeout
    while time.time() < deadline and len(hits) < expected_count:
        try:
            hits.append(recorder.hits.get(timeout=0.4))
        except queue.Empty:
            continue
    return hits


def _run_real_browser_probe(root: Path) -> None:
    server, recorder, url = _start_server()
    profile_dir = root / "profile" / ".ares-seleniumbase-cdp"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / ".ares-site-adapter.json").write_text(
        json.dumps({"root": "#grid", "tiles": ".tile", "submit": ".submit"}),
        encoding="utf-8",
    )

    client: WorkerClient | None = None
    try:
        client = WorkerClient(profile_dir, url)

        state: Dict[str, Any] = {}
        deadline = time.time() + 15.0
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            request_id = f"grid-state-{attempt}"
            client.send({"type": "site-grid-state", "requestId": request_id})
            state_message = client.wait("site-grid-state", request_id, 6)
            state = state_message.get("state") or {}
            if state.get("kind") == "image-grid" and int(state.get("tileCount") or 0) == 64:
                break
            time.sleep(0.25)

        assert state.get("kind") == "image-grid", state
        assert int(state.get("rows") or 0) == 8, state
        assert int(state.get("columns") or 0) == 8, state
        assert int(state.get("tileCount") or 0) == 64, state
        assert len([source for source in state.get("sources") or [] if source]) == 64, state

        # This probe verifies physical CDP execution, not a route predicted from a
        # stale observation. apply_grid_selection() deliberately re-polls the grid
        # immediately before acting, so its nearest-neighbour order belongs to that
        # action-time snapshot. The invariant that matters here is exact membership:
        # every requested tile once, no extras, followed by a real submit click.
        requested = [9, 8, 1, 0]
        clean_requested = ProximityGridActionExecutor._clean_indexes(requested, int(state["tileCount"]))

        request_id = "apply-selection"
        client.send({
            "type": "apply-grid-selection",
            "requestId": request_id,
            "indexes": requested,
            "submit": True,
        })
        execution = client.wait("grid-selection-applied", request_id, 20)

        clicked_indexes = [int(value) for value in execution.get("clickedIndexes") or []]
        click_order = [int(value) for value in execution.get("clickOrder") or []]
        assert len(clicked_indexes) == len(clean_requested), (execution, clean_requested)
        assert sorted(clicked_indexes) == sorted(clean_requested), (execution, clean_requested)
        assert click_order == clicked_indexes, execution
        assert execution.get("submitted") is True, execution

        hits = _collect_hits(recorder, len(clean_requested) + 1)
        tile_hits = [int(hit["index"]) for hit in hits if hit.get("type") == "tile"]
        submit_hits = [hit for hit in hits if hit.get("type") == "submit"]
        assert tile_hits == clicked_indexes, (hits, execution)
        assert sorted(tile_hits) == sorted(clean_requested), (hits, clean_requested)
        assert len(submit_hits) == 1, hits
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        server.shutdown()
        server.server_close()


def main() -> int:
    assert ExtendedGridSiteAdapter._infer_shape(_rects(4, 4)) == (4, 4)
    assert ExtendedGridSiteAdapter._infer_shape(_rects(8, 8)) == (8, 8)
    assert ExtendedGridSiteAdapter._infer_shape(_rects(2, 3)) == (2, 3)

    state = {"marks": _marks(4, 4)}
    order = ProximityGridActionExecutor._ordered_indexes(state, [15, 7, 3, 0])
    assert order == [0, 3, 7, 15], order

    classifier = RobustVisionGridClassifier()
    assert classifier._target_text("Klicke auf Ampeln") == "Ampeln"
    assert classifier._target_text("Click all traffic lights") == "traffic lights"
    assert classifier._target_text("Wähle alle Bilder mit Fahrrädern") == "Fahrrädern"

    temporary = Path(tempfile.mkdtemp(prefix="ares-readiness-grid-"))
    try:
        _run_real_browser_probe(temporary)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)

    print("PASS: real SeleniumBase CDP visual 8x8 grid clicks and submit verified by the browser test server.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
