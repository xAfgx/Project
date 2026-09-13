"""Long-running MediaMarkt GraphQL helper over curl_cffi.

Keeps ONE ``curl_cffi.Session`` for the whole process, so cookies (consent,
clearance, forter, ...) persist and refresh automatically across requests.
Reads newline-delimited JSON requests on stdin and writes newline-delimited JSON
responses on stdout. A single request also works (stdin closes -> process exits).

Request:
  {"id": 1, "url": "...", "operation": "SearchV4", "hash": "<sha256>",
   "cacheable": true, "clientName": "...", "clientVersion": "...",
   "cookieFile": "path.json", "variables": {...}, "pwa": {...}, "timeout": 30}

Response:
  {"id": 1, "ok": true, "status": 200, "data": {...}}
  {"id": 1, "ok": false, "status": 429, "error": "..."}
"""

import json
import sys
import uuid
from pathlib import Path

from curl_cffi import requests

DEFAULT_URL = "https://www.mediamarkt.de/api/v1/graphql"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

_session = requests.Session(impersonate="chrome")
_loaded_cookie_file = ""
_loaded_cookie_mtime = 0.0


def emit(value: dict) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def apply_cookies(cookies) -> int:
    count = 0
    if not isinstance(cookies, list):
        return 0
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name") or "").strip()
        if not name:
            continue
        try:
            _session.cookies.set(
                name,
                str(cookie.get("value") or ""),
                domain=cookie.get("domain") or None,
                path=cookie.get("path") or "/",
            )
            count += 1
        except Exception:
            pass
    return count


def refresh_cookie_file(path: str) -> None:
    """Reload the jar only when the file changed, so fresh harvests are picked up."""
    global _loaded_cookie_file, _loaded_cookie_mtime
    if not path:
        return
    try:
        file = Path(path)
        if not file.exists():
            return
        mtime = file.stat().st_mtime
        if path == _loaded_cookie_file and mtime == _loaded_cookie_mtime:
            return
        data = json.loads(file.read_text(encoding="utf-8"))
        apply_cookies(data)
        _loaded_cookie_file = path
        _loaded_cookie_mtime = mtime
    except Exception:
        pass


def handle(request: dict) -> dict:
    url = str(request.get("url") or DEFAULT_URL)
    operation = str(request.get("operation") or "SearchV4")
    query_hash = str(request.get("hash") or "")
    variables = request.get("variables") if isinstance(request.get("variables"), dict) else {}
    pwa = request.get("pwa") if isinstance(request.get("pwa"), dict) else {}
    if not query_hash:
        return {"ok": False, "error": "missing persisted query hash"}

    refresh_cookie_file(str(request.get("cookieFile") or "").strip())
    apply_cookies(request.get("cookies"))

    params = {
        "operationName": operation,
        "variables": json.dumps(variables, separators=(",", ":")),
        "extensions": json.dumps(
            {"persistedQuery": {"version": 1, "sha256Hash": query_hash}, "pwa": pwa},
            separators=(",", ":"),
        ),
    }
    headers = {
        "accept": "*/*",
        "content-type": "application/json",
        "x-mms-salesline": "Media",
        "x-mms-country": "DE",
        "x-mms-language": "de",
        "apollographql-client-name": str(request.get("clientName") or "pwa-client-pqm"),
        "apollographql-client-version": str(request.get("clientVersion") or "8.484.0"),
        "user-agent": DEFAULT_UA,
        "x-operation": operation,
        "x-cacheable": "true" if request.get("cacheable") is True else "false",
        "x-flow-id": str(uuid.uuid4()),
    }
    extra = request.get("headers")
    if isinstance(extra, dict):
        for key, value in extra.items():
            headers[str(key)] = str(value)

    try:
        response = _session.get(
            url,
            params=params,
            headers=headers,
            timeout=float(request.get("timeout") or 30),
        )
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    status = int(response.status_code)
    if status != 200:
        return {"ok": False, "status": status, "error": response.text[:500]}

    try:
        payload = response.json()
    except Exception as exc:
        return {"ok": False, "status": status, "error": f"invalid json: {exc}"}

    if payload.get("errors"):
        return {"ok": False, "status": status, "error": json.dumps(payload["errors"], ensure_ascii=False)[:700]}

    return {"ok": True, "status": status, "data": payload.get("data") or {}}


def main() -> int:
    emit({"type": "ready", "cookies": len(_session.cookies)})
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except Exception as exc:
            emit({"ok": False, "error": f"invalid request: {exc}"})
            continue
        if str(request.get("type") or "") == "close":
            break
        if str(request.get("type") or "") == "cookies":
            refresh_cookie_file(str(request.get("cookieFile") or "").strip())
            apply_cookies(request.get("cookies"))
            emit({
                "id": request.get("id"),
                "ok": True,
                "cookies": [
                    {"name": cookie.name, "value": cookie.value, "domain": cookie.domain or "", "path": cookie.path or "/"}
                    for cookie in _session.cookies
                ],
            })
            continue
        result = handle(request)
        result["id"] = request.get("id")
        emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
