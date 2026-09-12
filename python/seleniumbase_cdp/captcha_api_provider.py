"""External captcha-solving providers (CapMonster, 2Captcha, CapSolver, Anti-Captcha).

Reads the keys the Electron app pushes into the process environment
(CAPMONSTER_API_KEY, TWOCAPTCHA_API_KEY, CAPSOLVER_API_KEY,
ANTICAPTCHA_API_KEY) and the solve mode (ARES_CAPTCHA_MODE: siglip |
siglip-api | api). Every call is best-effort: failures return an empty token
and never raise into the browser flow.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

_PROVIDER_KEYS: Tuple[Tuple[str, str], ...] = (
    ("capmonster", "CAPMONSTER_API_KEY"),
    ("twocaptcha", "TWOCAPTCHA_API_KEY"),
    ("capsolver", "CAPSOLVER_API_KEY"),
    ("anticaptcha", "ANTICAPTCHA_API_KEY"),
    ("nocaptchaai", "NOCAPTCHA_API_KEY"),
)

# Modular provider registry. Each provider declares which solving capability it
# offers, so new services only need one entry (+ one implementation branch):
#   "token"    -> returns a captcha token that is injected into the page
#   "classify" -> returns tile indexes for an image grid (clicked locally)
PROVIDERS: Tuple[Dict[str, Any], ...] = (
    {"id": "capmonster", "name": "CapMonster Cloud", "env": "CAPMONSTER_API_KEY", "capabilities": ("token",)},
    {"id": "twocaptcha", "name": "2Captcha", "env": "TWOCAPTCHA_API_KEY", "capabilities": ("token",)},
    {"id": "capsolver", "name": "CapSolver", "env": "CAPSOLVER_API_KEY", "capabilities": ("token",)},
    {"id": "anticaptcha", "name": "Anti-Captcha", "env": "ANTICAPTCHA_API_KEY", "capabilities": ("token",)},
    {"id": "nocaptchaai", "name": "NoCaptchaAI", "env": "NOCAPTCHA_API_KEY", "capabilities": ("classify",)},
)


def provider_definition(provider_id: str) -> Optional[Dict[str, Any]]:
    for definition in PROVIDERS:
        if definition["id"] == provider_id:
            return definition
    return None


def providers_with(capability: str) -> List[Dict[str, Any]]:
    return [definition for definition in PROVIDERS if capability in definition["capabilities"]]


def _provider_env(provider_id: str) -> str:
    definition = provider_definition(provider_id)
    return str(definition.get("env") or "") if definition else ""


def configured_provider_for(capability: str) -> Tuple[str, str]:
    """First configured provider that offers the requested capability."""
    for definition in providers_with(capability):
        env_key = str(definition.get("env") or "")
        key = str(os.environ.get(env_key) or "").strip()
        if key:
            return str(definition["id"]), key
    return "", ""

_POLL_INTERVAL_SECONDS = 3.0


def captcha_mode() -> str:
    value = str(os.environ.get("ARES_CAPTCHA_MODE") or "").strip().lower()
    return value if value in {"siglip", "siglip-api", "api"} else "siglip"


def configured_provider() -> Tuple[str, str]:
    """First configured token-based provider (CapMonster/2Captcha/…)."""
    return configured_provider_for("token")


def classifier_available() -> bool:
    """True when a configured provider can classify image grids."""
    provider, key = configured_provider_for("classify")
    return bool(provider and key)


def classify_grid(
    *,
    images: List[str],
    question: str,
    url: str,
    timeout_seconds: float = 90.0,
) -> Dict[str, Any]:
    """Classify an image grid. Returns {"indexes": [...], "provider", "error"}."""
    provider, key = configured_provider_for("classify")
    if not provider or not key:
        return {"indexes": [], "provider": "", "error": "no-classifier-configured"}
    if not images:
        return {"indexes": [], "provider": provider, "error": "no-images"}
    try:
        if provider == "nocaptchaai":
            return _nocaptchaai_classify(key, images, question, url, timeout_seconds)
        return {"indexes": [], "provider": provider, "error": "classify-not-implemented"}
    except Exception as exc:  # pragma: no cover - defensive
        return {"indexes": [], "provider": provider, "error": f"{type(exc).__name__}: {exc}"[:200]}


def _solution_indexes(solution: Dict[str, Any], tile_count: int) -> List[int]:
    objects = solution.get("objects")
    if isinstance(objects, list):
        limit = max(1, int(tile_count))
        return [int(value) for value in objects if isinstance(value, (int, float)) and 0 <= int(value) < limit]
    flags = solution.get("hasObject")
    if isinstance(flags, list):
        return [index for index, flag in enumerate(flags) if flag]
    return []


def _nocaptchaai_classify(
    key: str,
    images: List[str],
    question: str,
    url: str,
    timeout_seconds: float,
) -> Dict[str, Any]:
    """NoCaptchaAI image classification (ReCaptchaV2Classification)."""
    count = len(images)
    payload = {
        "clientKey": key,
        "task": {
            "type": "ReCaptchaV2Classification",
            "questionType": "44" if count >= 13 else "33",
            "image": list(images),
            "question": question,
            "websiteURL": url,
        },
    }
    response = _post_json("https://api.nocaptchaai.com/createTask", payload)
    if int(response.get("errorId") or 0) != 0:
        return {
            "indexes": [],
            "provider": "nocaptchaai",
            "error": str(response.get("error") or response.get("errorCode") or "task-error")[:120],
        }
    indexes = _solution_indexes(response.get("solution") or {}, count)
    if indexes:
        return {"indexes": indexes, "provider": "nocaptchaai", "error": ""}
    task_id = str(response.get("taskId") or "")
    deadline = time.monotonic() + max(10.0, min(240.0, timeout_seconds))
    while task_id and time.monotonic() < deadline:
        time.sleep(_POLL_INTERVAL_SECONDS)
        poll = _post_json("https://api.nocaptchaai.com/getTaskResult", {"clientKey": key, "taskId": task_id})
        indexes = _solution_indexes(poll.get("solution") or {}, count)
        if indexes:
            return {"indexes": indexes, "provider": "nocaptchaai", "error": ""}
        status = str(poll.get("status") or "").lower()
        if status in {"failed", "error"}:
            return {
                "indexes": [],
                "provider": "nocaptchaai",
                "error": str(poll.get("error") or poll.get("errorCode") or status)[:120],
            }
    return {"indexes": [], "provider": "nocaptchaai", "error": "empty-solution"}


def provider_available() -> bool:
    provider, key = configured_provider()
    return bool(provider and key)


def solve_token(
    *,
    kind: str,
    sitekey: str,
    url: str,
    timeout_seconds: float = 120.0,
) -> Dict[str, Any]:
    """Return {"token": str, "provider": str, "error": str}.

    `kind` is "recaptcha" or "hcaptcha". The first configured provider wins.
    """
    provider, key = configured_provider()
    if not provider or not key:
        return {"token": "", "provider": "", "error": "no-provider-configured"}
    if not sitekey or not url:
        return {"token": "", "provider": provider, "error": "missing-sitekey-or-url"}
    try:
        if provider == "nocaptchaai":
            return _nocaptchaai_solve(key, kind, sitekey, url, timeout_seconds)
        if provider == "twocaptcha":
            task_id = _twocaptcha_create(provider, key, kind, sitekey, url)
        else:
            task_id = _task_create(provider, key, kind, sitekey, url)
        if not task_id:
            return {"token": "", "provider": provider, "error": "task-create-failed"}
        deadline = time.monotonic() + max(10.0, min(240.0, timeout_seconds))
        while time.monotonic() < deadline:
            time.sleep(_POLL_INTERVAL_SECONDS)
            if provider == "twocaptcha":
                token, error = _twocaptcha_result(provider, key, task_id)
            else:
                token, error = _task_result(provider, key, task_id)
            if token:
                return {"token": token, "provider": provider, "error": ""}
            if error:
                return {"token": "", "provider": provider, "error": error}
        return {"token": "", "provider": provider, "error": "timeout"}
    except Exception as exc:  # pragma: no cover - defensive: never break the flow
        return {"token": "", "provider": provider, "error": f"{type(exc).__name__}: {exc}"[:200]}


def _post_json(url: str, payload: Dict[str, Any], timeout: float = 15.0) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        # Providers signal errors with non-2xx statuses but still return a
        # useful JSON body (e.g. NoCaptchaAI: {"error": "Invalid apikey"}).
        try:
            parsed = json.loads(error.read().decode("utf-8", "replace"))
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        raise


def _get_json(url: str, timeout: float = 15.0) -> Dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _task_type(provider: str, kind: str) -> str:
    if kind == "hcaptcha":
        return "HCaptchaTaskProxyless"
    if provider == "capsolver":
        return "ReCaptchaV2TaskProxyLess"
    return "NoCaptchaTaskProxyless"


def _task_endpoint(provider: str) -> str:
    if provider == "capsolver":
        return "https://api.capsolver.com"
    if provider == "anticaptcha":
        return "https://api.anti-captcha.com"
    return "https://api.capmonster.cloud"


def _task_create(provider: str, key: str, kind: str, sitekey: str, url: str) -> str:
    base = _task_endpoint(provider)
    payload = {
        "clientKey": key,
        "task": {
            "type": _task_type(provider, kind),
            "websiteURL": url,
            "websiteKey": sitekey,
        },
    }
    if provider == "capsolver":
        payload["appId"] = "ares"
    response = _post_json(f"{base}/createTask", payload)
    if int(response.get("errorId") or 0) != 0:
        return ""
    return str(response.get("taskId") or "")


def _task_result(provider: str, key: str, task_id: str) -> Tuple[str, str]:
    base = _task_endpoint(provider)
    response = _post_json(f"{base}/getTaskResult", {"clientKey": key, "taskId": task_id})
    if int(response.get("errorId") or 0) != 0:
        return "", str(response.get("errorCode") or "task-error")
    status = str(response.get("status") or "")
    if status == "ready":
        solution = response.get("solution") or {}
        token = str(solution.get("gRecaptchaResponse") or solution.get("token") or "")
        return token, "" if token else "empty-solution"
    return "", ""


def _twocaptcha_create(provider: str, key: str, kind: str, sitekey: str, url: str) -> str:
    method = "hcaptcha" if kind == "hcaptcha" else "userrecaptcha"
    query = urllib.parse.urlencode({
        "key": key,
        "method": method,
        "sitekey": sitekey,
        "pageurl": url,
        "json": 1,
    })
    response = _get_json(f"https://2captcha.com/in.php?{query}")
    if int(response.get("status") or 0) != 1:
        return ""
    return str(response.get("request") or "")


def _twocaptcha_result(provider: str, key: str, task_id: str) -> Tuple[str, str]:
    query = urllib.parse.urlencode({"key": key, "action": "get", "id": task_id, "json": 1})
    response = _get_json(f"https://2captcha.com/res.php?{query}")
    if int(response.get("status") or 0) == 1:
        token = str(response.get("request") or "")
        return token, "" if token else "empty-solution"
    request_text = str(response.get("request") or "")
    if request_text and request_text.upper() != "CAPCHA_NOT_READY":
        return "", request_text[:80]
    return "", ""


def status() -> Dict[str, Any]:
    provider, _ = configured_provider()
    return {
        "mode": captcha_mode(),
        "provider": provider,
        "available": bool(provider),
        "keys": {pid: bool(str(os.environ.get(env) or "").strip()) for pid, env in _PROVIDER_KEYS},
    }


def _post_json_headers(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: float = 20.0) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    merged = {"Content-Type": "application/json", **headers}
    request = urllib.request.Request(url, data=data, headers=merged, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _nocaptchaai_solve(key: str, kind: str, sitekey: str, url: str, timeout_seconds: float) -> Dict[str, Any]:
    """NoCaptchaAI (https://nocaptchaai.com): POST /solve with an apikey header."""
    endpoint = "https://api.nocaptchaai.com/solve"
    headers = {"apikey": key}
    method = "hcaptcha" if kind == "hcaptcha" else "userrecaptcha"
    body: Dict[str, Any] = {"method": method, "sitekey": sitekey, "pageurl": url}
    response = _post_json_headers(endpoint, body, headers)
    token = _nocaptchaai_token(response)
    if token:
        return {"token": token, "provider": "nocaptchaai", "error": ""}
    task_id = str(response.get("id") or response.get("taskId") or "")
    status = str(response.get("status") or "").lower()
    if not task_id or status in {"invalid", "error"}:
        return {"token": "", "provider": "nocaptchaai", "error": str(response.get("message") or status or "task-create-failed")[:120]}
    deadline = time.monotonic() + max(10.0, min(240.0, timeout_seconds))
    while time.monotonic() < deadline:
        time.sleep(_POLL_INTERVAL_SECONDS)
        try:
            poll = _post_json_headers(endpoint, {"id": task_id}, headers)
        except Exception:
            poll = {}
        token = _nocaptchaai_token(poll)
        if token:
            return {"token": token, "provider": "nocaptchaai", "error": ""}
        status = str(poll.get("status") or "").lower()
        if status in {"invalid", "error"}:
            return {"token": "", "provider": "nocaptchaai", "error": str(poll.get("message") or status)[:120]}
    return {"token": "", "provider": "nocaptchaai", "error": "timeout"}


def _nocaptchaai_token(response: Dict[str, Any]) -> str:
    if not isinstance(response, dict):
        return ""
    solution = response.get("solution")
    if isinstance(solution, dict):
        return str(solution.get("token") or solution.get("gRecaptchaResponse") or "")
    return str(solution or response.get("token") or response.get("gRecaptchaResponse") or "")


def apply_runtime_config(payload: Any) -> bool:
    """Apply a live config pushed from the UI via the worker RPC.

    Accepts {"mode": "siglip|siglip-api|api", "keys": {"CAPMONSTER_API_KEY": "..."}}.
    Unknown keys are ignored; empty values clear the env entry.
    """
    if not isinstance(payload, dict):
        return False
    changed = False
    mode = str(payload.get("mode") or "").strip().lower()
    if mode in {"siglip", "siglip-api", "api"}:
        os.environ["ARES_CAPTCHA_MODE"] = mode
        changed = True
    keys = payload.get("keys")
    if isinstance(keys, dict):
        known = {env_key for _pid, env_key in _PROVIDER_KEYS}
        for env_key, value in keys.items():
            name = str(env_key or "").strip()
            if name not in known:
                continue
            text_value = str(value or "").strip()
            if text_value:
                os.environ[name] = text_value
            else:
                os.environ.pop(name, None)
            changed = True
    return changed
