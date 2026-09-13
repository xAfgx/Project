"""One-shot MediaMarkt GraphQL caller over curl_cffi.

Reads a JSON request from stdin and prints a JSON response to stdout. Used by
the Node product-API adapter so MediaMarkt requests carry a Chrome TLS
fingerprint without running a browser.

Request:
  {
    "url": "https://www.mediamarkt.de/api/v1/graphql",
    "operation": "SearchV4",
    "hash": "<persisted query sha256>",
    "cacheable": true,
    "variables": { ... },
    "pwa": { ... },
    "clientName": "pwa-client-pqm",
    "clientVersion": "8.484.0"
  }

Response:
  {"ok": true, "status": 200, "data": {...}}
  {"ok": false, "status": 429, "error": "..."}
"""

import json
import sys
import uuid

from curl_cffi import requests

DEFAULT_URL = "https://www.mediamarkt.de/api/v1/graphql"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)


def emit(value: dict) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    try:
        raw = sys.stdin.read()
        request = json.loads(raw) if raw.strip() else {}
    except Exception as exc:
        emit({"ok": False, "error": f"invalid request: {exc}"})
        return 1

    url = str(request.get("url") or DEFAULT_URL)
    operation = str(request.get("operation") or "SearchV4")
    query_hash = str(request.get("hash") or "")
    variables = request.get("variables") if isinstance(request.get("variables"), dict) else {}
    pwa = request.get("pwa") if isinstance(request.get("pwa"), dict) else {}

    if not query_hash:
        emit({"ok": False, "error": "missing persisted query hash"})
        return 1

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

    # Clearance/consent cookies harvested from the browser profile. Accepts an
    # inline list or a JSON file with [{name, value, domain, path}, ...].
    cookies: list[dict] = []
    inline = request.get("cookies")
    if isinstance(inline, list):
        cookies.extend([c for c in inline if isinstance(c, dict)])
    cookie_file = str(request.get("cookieFile") or "").strip()
    if cookie_file:
        try:
            with open(cookie_file, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, list):
                cookies.extend([c for c in loaded if isinstance(c, dict)])
        except Exception:
            pass

    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            cookies=cookies or None,
            impersonate=str(request.get("impersonate") or "chrome"),
            timeout=float(request.get("timeout") or 30),
        )
    except Exception as exc:
        emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1

    status = int(response.status_code)
    if status != 200:
        emit({"ok": False, "status": status, "error": response.text[:500]})
        return 1

    try:
        payload = response.json()
    except Exception as exc:
        emit({"ok": False, "status": status, "error": f"invalid json: {exc}"})
        return 1

    if payload.get("errors"):
        emit({"ok": False, "status": status, "error": json.dumps(payload["errors"], ensure_ascii=False)[:700]})
        return 1

    emit({"ok": True, "status": status, "data": payload.get("data") or {}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
