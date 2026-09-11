from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

PREFIX = "ARES_SESSION_HTTP\t"
JAR_FILENAME = ".ares-session-jar.json"
JAR_TTL_SECONDS = 20
ALLOWED_ENGINE = "curl_cffi"
ALLOWED_INSTANCE_MODE = "monitor"
QUEUE_RE = re.compile(r"(?i)(queue[-_.]?it|waiting[-_.]?room|warteschlange|queue-position|queue/status|\"ttw\"|\"position\")")
RELEASE_RE = re.compile(r"(?i)(released|complete|completed|redirect|passed|admitted)")
SOLD_OUT_RE = re.compile(r"(?i)(sold[\s_-]?out|out[\s_-]?of[\s_-]?stock|ausverkauft|nicht[\s-]?(?:auf[\s-]?)?vorrätig|nicht[\s-]?verfügbar|nicht[\s-]?verfuegbar|currently[\s-]?unavailable)")
AVAILABLE_RE = re.compile(r"(?i)(add[\s_-]?to[\s_-]?cart|add[\s_-]?to[\s_-]?bag|in[\s_-]?den[\s_-]?warenkorb|in[\s_-]?den[\s_-]?einkaufswagen|buy[\s_-]?now|jetzt[\s-]?kaufen|auf[\s-]?lager|in[\s-]?stock|vorrätig|vorraetig)")


def emit(value: dict[str, Any]) -> None:
    print(PREFIX + json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


class EngineBlocked(PermissionError):
    """Raised when curl_cffi is requested outside an explicit UI monitor run."""


def engine_allowed(command: dict[str, Any]) -> bool:
    """Strict engine switch: curl_cffi only for UI-started MONITOR instances.

    Every other instance (manual browser, live tests, early gate, checkout
    tasks) must use harvest=cdp through the native browser infrastructure.
    """
    return (
        str(command.get("engine") or "").strip().lower() == ALLOWED_ENGINE
        and str(command.get("instanceMode") or "").strip().lower() == ALLOWED_INSTANCE_MODE
    )


def assert_engine_allowed(command: dict[str, Any]) -> None:
    if not engine_allowed(command):
        raise EngineBlocked(
            "engine-blocked: curl_cffi is only permitted for UI-started MONITOR "
            "instances; all other modes run harvest=cdp via the native browser"
        )


def _num(value: Any) -> float | None:
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(value or ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def classify(url: str, body: str) -> dict[str, Any]:
    position = None
    ttw = None
    status = ""
    try:
        payload = json.loads(body)
        if isinstance(payload, dict):
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            position = _num(payload.get("pos") or payload.get("position") or data.get("pos") or data.get("position"))
            ttw = _num(payload.get("ttw") or payload.get("timeToWait") or data.get("ttw") or data.get("timeToWait"))
            status = str(payload.get("status") or data.get("status") or "").strip()
    except Exception:
        pass

    released = bool(status and RELEASE_RE.search(status))
    active = False if released else bool(
        QUEUE_RE.search(url)
        or QUEUE_RE.search(body[:256000])
        or position is not None
        or ttw is not None
        or (status and re.search(r"(?i)(queue|wait|position|hold)", status))
    )
    return {"active": active, "position": position, "timeToWaitSeconds": ttw, "statusText": status or None}


def classify_product(body: str) -> dict[str, Any]:
    if SOLD_OUT_RE.search(body[:256000]):
        return {"available": False, "productState": "sold-out"}
    if AVAILABLE_RE.search(body[:256000]):
        return {"available": True, "productState": "available"}
    return {"available": None, "productState": "unknown"}


def jar_path(profile_dir: Path) -> Path:
    return profile_dir / JAR_FILENAME


def load_jar(profile_dir: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(jar_path(profile_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or not value.get("userAgent") or not isinstance(value.get("cookies"), list):
        return None
    return value


def write_jar(profile_dir: Path, user_agent: str, cookies: list[dict[str, Any]], client_hints: dict[str, Any] | None = None) -> None:
    payload = {
        "userAgent": user_agent,
        "cookies": cookies,
        "clientHints": client_hints or {},
        "harvestedAtEpochMs": int(time.time() * 1000),
    }
    try:
        jar_path(profile_dir).write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass


def runtime_meta(profile_dir: Path) -> dict[str, Any]:
    path = profile_dir / ".ares-browser-runtime.json"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("cdpPort"):
                return value
        except Exception:
            pass
        time.sleep(0.05)
    raise RuntimeError("browser runtime metadata unavailable")


def browser_ws(port: int) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as response:
        value = json.loads(response.read().decode("utf-8"))
    ws = str(value.get("webSocketDebuggerUrl") or "")
    if not ws:
        raise RuntimeError("browser CDP websocket unavailable")
    return ws


async def cdp_session(port: int, url: str) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    import websockets

    async with websockets.connect(browser_ws(port), ping_timeout=20, max_size=2**22) as ws:
        next_id = 0

        async def call(method: str, params: dict[str, Any] | None = None, session_id: str | None = None) -> dict[str, Any]:
            nonlocal next_id
            next_id += 1
            payload: dict[str, Any] = {"id": next_id, "method": method, "params": params or {}}
            if session_id:
                payload["sessionId"] = session_id
            await ws.send(json.dumps(payload, separators=(",", ":")))
            while True:
                reply = json.loads(await ws.recv())
                if int(reply.get("id") or 0) != next_id:
                    continue
                if isinstance(reply.get("error"), dict):
                    raise RuntimeError(str(reply["error"].get("message") or "CDP command failed"))
                result = reply.get("result")
                return result if isinstance(result, dict) else {}

        version = await call("Browser.getVersion")
        ua = str(version.get("userAgent") or "")
        targets = await call("Target.getTargets")
        infos = targets.get("targetInfos") if isinstance(targets.get("targetInfos"), list) else []
        target_id = next((str(item.get("targetId") or "") for item in infos if isinstance(item, dict) and item.get("type") == "page" and item.get("targetId")), "")
        if not target_id:
            raise RuntimeError("page target unavailable")
        attached = await call("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        session_id = str(attached.get("sessionId") or "")
        if not session_id:
            raise RuntimeError("CDP attach failed")
        try:
            await call("Network.enable", {}, session_id)
            client_hints: dict[str, Any] = {}
            try:
                metadata = await call("Network.getUserAgentMetadata", {}, session_id)
                if isinstance(metadata, dict):
                    brands = metadata.get("brands")
                    if isinstance(brands, list) and brands:
                        parts = []
                        for brand_item in brands:
                            if isinstance(brand_item, dict):
                                brand = str(brand_item.get("brand") or "")
                                version_value = str(brand_item.get("version") or "")
                                if brand and version_value:
                                    parts.append(f'"{brand}";v="{version_value}"')
                        if parts:
                            client_hints["sec_ch_ua"] = ", ".join(parts)
                    client_hints["sec_ch_ua_mobile"] = "?1" if metadata.get("mobile") else "?0"
                    platform = str(metadata.get("platform") or "").strip()
                    if platform:
                        client_hints["sec_ch_ua_platform"] = f'"{platform}"'
            except Exception:
                pass
            cookies = await call("Network.getCookies", {"urls": [url]}, session_id)
            raw = cookies.get("cookies") if isinstance(cookies.get("cookies"), list) else []
            return ua, [item for item in raw if isinstance(item, dict)], client_hints
        finally:
            try:
                await call("Target.detachFromTarget", {"sessionId": session_id})
            except Exception:
                pass


def resolve_session(profile_dir: Path, url: str) -> tuple[str, list[dict[str, Any]], dict[str, Any], str]:
    jar = load_jar(profile_dir)
    if jar is not None:
        age_ms = int(time.time() * 1000) - int(jar.get("harvestedAtEpochMs") or 0)
        if 0 <= age_ms < JAR_TTL_SECONDS * 1000:
            return str(jar["userAgent"]), list(jar["cookies"]), dict(jar.get("clientHints") or {}), "jar"

    # Jar missing or stale: re-harvest from the live browser via CDP. If the
    # browser has already closed, fall back to the stale jar so polling can
    # continue without reopening a browser.
    try:
        meta = runtime_meta(profile_dir)
        ua, cookies, client_hints = asyncio.run(cdp_session(int(meta.get("cdpPort") or 0), url))
        if ua:
            write_jar(profile_dir, ua, cookies, client_hints)
        return ua, cookies, client_hints, "cdp"
    except Exception:
        if jar is not None:
            return str(jar["userAgent"]), list(jar["cookies"]), dict(jar.get("clientHints") or {}), "jar-stale"
        raise


def impersonate_for(user_agent: str) -> str:
    from curl_cffi.requests import BrowserType

    match = re.search(r"Chrome/(\d+)", user_agent or "")
    if not match:
        return "chrome"
    major = int(match.group(1))
    supported = sorted(
        int(name[6:])
        for name in dir(BrowserType)
        if name.startswith("chrome") and name[6:].isdigit()
    )
    best = max((version for version in supported if version <= major), default=None)
    return f"chrome{best}" if best else "chrome"


def poll(command: dict[str, Any]) -> dict[str, Any]:
    assert_engine_allowed(command)
    from curl_cffi import requests

    url = str(command.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("http(s) url required")
    profile_dir = Path(str(command.get("profileDir") or "")).expanduser().resolve()
    ua, cookies, client_hints, harvest_source = resolve_session(profile_dir, url)
    cookie_names = [str(c.get("name") or "") for c in cookies]
    has_clearance = any("datadome" in name.lower() or "die-" in name.lower() for name in cookie_names)

    headers: dict[str, Any] = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8,en-US;q=0.7",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "upgrade-insecure-requests": "1",
    }
    if client_hints.get("sec_ch_ua"):
        headers["sec-ch-ua"] = client_hints["sec_ch_ua"]
    if client_hints.get("sec_ch_ua_mobile"):
        headers["sec-ch-ua-mobile"] = client_hints["sec_ch_ua_mobile"]
    if client_hints.get("sec_ch_ua_platform"):
        headers["sec-ch-ua-platform"] = client_hints["sec_ch_ua_platform"]
    cookie_header = "; ".join(f"{c.get('name')}={c.get('value', '')}" for c in cookies if c.get("name"))
    if cookie_header:
        headers["Cookie"] = cookie_header

    kwargs: dict[str, Any] = {"headers": headers, "timeout": 6, "allow_redirects": True, "impersonate": impersonate_for(ua)}
    proxy = str(command.get("proxy") or "").strip()
    if proxy:
        kwargs["proxy"] = proxy

    response = requests.get(url, **kwargs)
    body = str(response.text or "")[:256000]
    queue = classify(str(response.url or url), body)
    product = classify_product(body)
    return {
        "type": "probe",
        "ok": True,
        "source": "session-http",
        "statusCode": int(response.status_code),
        "url": str(response.url or url),
        "cookieCount": len(cookies),
        "hasClearance": has_clearance,
        "harvestSource": harvest_source,
        "observedAtMs": int(time.time() * 1000),
        **queue,
        **product,
    }


def main() -> int:
    line = sys.stdin.readline()
    if not line:
        return 2
    command = json.loads(line)
    if not engine_allowed(command):
        emit({
            "type": "probe",
            "ok": False,
            "active": False,
            "source": "session-http",
            "engine": str(command.get("engine") or ""),
            "instanceMode": str(command.get("instanceMode") or ""),
            "harvestSource": "cdp",
            "error": "engine-blocked: curl_cffi is only permitted for UI-started MONITOR instances",
            "observedAtMs": int(time.time() * 1000),
        })
        return 3
    interval = max(1000, min(10000, int(command.get("pollIntervalMs") or 2000))) / 1000
    while True:
        started = time.monotonic()
        try:
            emit(poll(command))
        except Exception as exc:
            emit({"type": "probe", "ok": False, "active": False, "source": "session-http", "errorType": type(exc).__name__, "error": str(exc)[:1000], "observedAtMs": int(time.time() * 1000)})
        time.sleep(max(0.05, interval - (time.monotonic() - started)))


if __name__ == "__main__":
    raise SystemExit(main())
