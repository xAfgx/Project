from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

from vision_grid_classifier import VisionGridClassifier


MAX_BODY = 24 * 1024 * 1024


def _timing(event: str, **payload: Any) -> None:
    print(
        "ARES_VISION_TIMING\t" + json.dumps(
            {"event": event, "wall": time.time(), "mono": time.monotonic(), **payload},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        file=sys.stderr,
        flush=True,
    )


class VisionService:
    def __init__(self, token: str) -> None:
        self.token = token
        self.classifier = VisionGridClassifier(allow_remote=False)
        self._requests = 0
        self._lock = threading.Lock()
        self._preload_started = False

    def authorize(self, header: str) -> bool:
        return bool(self.token) and header == f"Bearer {self.token}"

    def preload_async(self) -> None:
        with self._lock:
            if self._preload_started:
                return
            self._preload_started = True

        def run() -> None:
            started = time.monotonic()
            _timing("model-load-start", model=self.classifier.model_name)
            ready = self.classifier.ready
            _timing(
                "model-load-end",
                model=self.classifier.model_name,
                ready=bool(ready),
                durationMs=round((time.monotonic() - started) * 1000.0, 3),
                error=self.classifier.error,
            )

        threading.Thread(target=run, name="ares-vision-preload", daemon=True).start()

    def health(self) -> Dict[str, Any]:
        value = self.classifier.status()
        with self._lock:
            requests = self._requests
            preload_started = self._preload_started
        return {
            **value,
            "service": "ares-shared-vision",
            "requests": requests,
            "preloadStarted": preload_started,
        }

    def classify(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        instruction = str(payload.get("instruction") or "")
        raw_sources = payload.get("sources")
        if not isinstance(raw_sources, list):
            raise TypeError("sources must be an array")
        if len(raw_sources) > 64:
            raise ValueError("vision request exceeds 64 tiles")
        sources = [str(value or "") for value in raw_sources]
        with self._lock:
            self._requests += 1
            request_number = self._requests
        started = time.monotonic()
        _timing("inference-start", request=request_number, tiles=len(sources))
        try:
            result = self.classifier.classify(instruction, sources)
            _timing(
                "inference-end",
                request=request_number,
                tiles=len(sources),
                durationMs=round((time.monotonic() - started) * 1000.0, 3),
                selectedIndexes=result.get("selectedIndexes") or [],
                error=str(result.get("error") or ""),
            )
            return result
        except Exception as exc:
            _timing(
                "inference-error",
                request=request_number,
                tiles=len(sources),
                durationMs=round((time.monotonic() - started) * 1000.0, 3),
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

    def classify_reference(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        reference_source = str(payload.get("referenceSource") or "")
        raw_sources = payload.get("sources")
        if not isinstance(raw_sources, list):
            raise TypeError("sources must be an array")
        if len(raw_sources) > 64:
            raise ValueError("vision request exceeds 64 tiles")
        sources = [str(value or "") for value in raw_sources]
        with self._lock:
            self._requests += 1
            request_number = self._requests
        started = time.monotonic()
        _timing("inference-start", request=request_number, tiles=len(sources), mode="reference")
        try:
            result = self.classifier.classify_reference(reference_source, sources)
            _timing(
                "inference-end",
                request=request_number,
                tiles=len(sources),
                durationMs=round((time.monotonic() - started) * 1000.0, 3),
                selectedIndexes=result.get("selectedIndexes") or [],
                error=str(result.get("error") or ""),
            )
            return result
        except Exception as exc:
            _timing(
                "inference-error",
                request=request_number,
                tiles=len(sources),
                durationMs=round((time.monotonic() - started) * 1000.0, 3),
                error=f"{type(exc).__name__}: {exc}",
            )
            raise


def handler_for(service: VisionService):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ARES-Vision/1"

        def log_message(self, _format: str, *args: Any) -> None:
            return

        def _json(self, status: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if service.authorize(str(self.headers.get("Authorization") or "")):
                return True
            self._json(403, {"error": "forbidden"})
            return False

        def do_GET(self) -> None:
            if self.path != "/health":
                self._json(404, {"error": "not-found"})
                return
            if not self._authorized():
                return
            self._json(200, service.health())

        def do_POST(self) -> None:
            if self.path not in {"/classify", "/classify-reference"}:
                self._json(404, {"error": "not-found"})
                return
            if not self._authorized():
                return
            try:
                length = int(self.headers.get("Content-Length") or "0")
                if length <= 0 or length > MAX_BODY:
                    raise ValueError("invalid request size")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise TypeError("request must be a JSON object")
                if self.path == "/classify-reference":
                    result = service.classify_reference(payload)
                else:
                    result = service.classify(payload)
                self._json(200, result)
            except Exception as exc:
                self._json(400, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--token", required=True)
    parser.add_argument("--preload", action="store_true")
    args = parser.parse_args()

    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("shared vision service must bind to loopback")

    service = VisionService(str(args.token))
    server = ThreadingHTTPServer((args.host, args.port), handler_for(service))
    host, port = server.server_address[:2]

    print(json.dumps({
        "ready": True,
        "url": f"http://{host}:{port}",
        "model": service.classifier.model_name,
        "preloading": bool(args.preload),
        "selectionPolicy": "hf-joint-forward-sigmoid",
        "promptTemplates": service.classifier.status().get("promptTemplates") or [],
    }), flush=True)
    if args.preload:
        service.preload_async()

    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
