from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List

import psutil

RUNTIME_ENV = "ARES_BROWSER_SESSION_ID"
RUNTIME_ARG_PREFIX = "--ares-session-id="
DEBUG_PORT_PREFIX = "--remote-debugging-port="
RUNTIME_FILENAME = ".ares-browser-runtime.json"
_BROWSER_NAME_MARKERS = ("chrome", "chromium", "msedge")


class BrowserRuntimeIdentity:
    """ARES-owned identity for one Python worker -> one Chromium runtime."""

    def __init__(self, profile_dir: str | Path, session_id: str | None = None) -> None:
        self.profile_dir = Path(profile_dir).expanduser().resolve()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = str(session_id or uuid.uuid4().hex).strip() or uuid.uuid4().hex
        self.worker_pid = os.getpid()
        self.started_at_epoch_ms = int(time.time() * 1000)
        os.environ[RUNTIME_ENV] = self.session_id
        self._write({
            "state": "starting",
            "runtimeSessionId": self.session_id,
            "workerPid": self.worker_pid,
            "profileDir": str(self.profile_dir),
            "startedAtEpochMs": self.started_at_epoch_ms,
        })

    @classmethod
    def create(cls, profile_dir: str | Path, requested_id: str | None = None) -> "BrowserRuntimeIdentity":
        return cls(profile_dir, requested_id)

    @property
    def marker_arg(self) -> str:
        return f"{RUNTIME_ARG_PREFIX}{self.session_id}"

    def browser_args(self, values: Iterable[str] | None = None) -> List[str]:
        args = [str(value).strip() for value in (values or []) if str(value).strip()]
        args = [value for value in args if not value.startswith(RUNTIME_ARG_PREFIX)]
        args.append(self.marker_arg)
        return args

    def browser_pids(self) -> List[int]:
        direct_matches: List[int] = []
        inherited_matches: List[int] = []
        profile_matches: List[int] = []
        target_profile = os.path.normcase(str(self.profile_dir))

        for process in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = str(process.info.get("name") or "").lower()
                if not any(marker in name for marker in _BROWSER_NAME_MARKERS):
                    continue
                command_line = [str(value) for value in (process.info.get("cmdline") or [])]
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error):
                continue

            pid = int(process.pid)
            if self.marker_arg in command_line:
                direct_matches.append(pid)

            for index, argument in enumerate(command_line):
                profile_value = ""
                if argument.startswith("--user-data-dir="):
                    profile_value = argument.split("=", 1)[1]
                elif argument == "--user-data-dir" and index + 1 < len(command_line):
                    profile_value = command_line[index + 1]
                if not profile_value:
                    continue
                clean = profile_value.strip().strip('"')
                try:
                    candidate = os.path.normcase(str(Path(clean).expanduser().resolve()))
                except (OSError, RuntimeError):
                    candidate = os.path.normcase(os.path.abspath(os.path.expanduser(clean)))
                if candidate == target_profile:
                    profile_matches.append(pid)
                    break

            try:
                environment = process.environ()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error, NotImplementedError):
                environment = {}
            if str(environment.get(RUNTIME_ENV) or "") == self.session_id:
                inherited_matches.append(pid)

        # Chromium does not reliably preserve an inspectable environment/session
        # marker on every Windows child process (notably crashpad/GPU/utility and
        # renderer children). The exact ARES profile directory is therefore a
        # second ownership proof, not a broad browser-name fallback. Profile
        # leasing guarantees one active ARES owner for this directory.
        return list(dict.fromkeys([*direct_matches, *inherited_matches, *profile_matches]))

    def capture_owned_pids(self, adapter: Any) -> List[int]:
        matches = self.browser_pids()
        chrome_pid = getattr(adapter, "chrome_pid", None)
        if isinstance(chrome_pid, int) and chrome_pid > 0:
            matches = [pid for pid in matches if pid != chrome_pid]
            matches.insert(0, chrome_pid)
        if matches:
            return matches
        fallback = getattr(adapter, "_profile_browser_pids", None)
        if callable(fallback):
            try:
                return list(dict.fromkeys(int(pid) for pid in fallback() if int(pid) > 0))
            except Exception:
                pass
        return []

    def cdp_port(self, adapter: Any) -> int | None:
        sb = getattr(adapter, "_sb", None)
        getter = getattr(sb, "get_rd_port", None)
        if callable(getter):
            try:
                value = int(getter())
                if 0 < value <= 65535:
                    return value
            except Exception:
                pass

        for pid in self.capture_owned_pids(adapter):
            try:
                command_line = [str(value) for value in psutil.Process(pid).cmdline()]
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error):
                continue
            for argument in command_line:
                if not argument.startswith(DEBUG_PORT_PREFIX):
                    continue
                try:
                    value = int(argument.split("=", 1)[1])
                except (IndexError, ValueError):
                    continue
                if 0 < value <= 65535:
                    return value
        return None

    def browser_websocket_url(self, adapter: Any) -> str:
        sb = getattr(adapter, "_sb", None)
        endpoint_getter = getattr(sb, "get_endpoint_url", None)
        endpoint = ""
        if callable(endpoint_getter):
            try:
                endpoint = str(endpoint_getter() or "").strip().rstrip("/")
            except Exception:
                endpoint = ""
        if not endpoint:
            port = self.cdp_port(adapter)
            if port:
                endpoint = f"http://127.0.0.1:{port}"
        if not endpoint:
            return ""

        request = urllib.request.Request(
            f"{endpoint}/json/version",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=2.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, UnicodeDecodeError):
            return ""
        websocket_url = str(payload.get("webSocketDebuggerUrl") or "").strip()
        if not websocket_url.startswith(("ws://", "wss://")):
            return ""
        return websocket_url

    def _publish_browser_websocket(self, adapter: Any, websocket_url: str) -> None:
        if not websocket_url:
            return
        sb = getattr(adapter, "_sb", None)
        driver = getattr(sb, "driver", None)
        if driver is not None and hasattr(driver, "cdp_base"):
            driver = driver.cdp_base
        if driver is None:
            return
        try:
            setattr(driver, "websocket_url", websocket_url)
        except Exception:
            pass

    def ready_metadata(self, adapter: Any, startup_ms: int | float | None = None) -> Dict[str, Any]:
        browser_pid = getattr(adapter, "chrome_pid", None)
        websocket_url = self.browser_websocket_url(adapter)
        self._publish_browser_websocket(adapter, websocket_url)
        metadata: Dict[str, Any] = {
            "state": "ready",
            "runtimeSessionId": self.session_id,
            "workerPid": self.worker_pid,
            "browserPid": int(browser_pid) if isinstance(browser_pid, int) and browser_pid > 0 else None,
            "cdpHost": "127.0.0.1",
            "cdpPort": self.cdp_port(adapter),
            "browserWebSocketReady": bool(websocket_url),
            "profileDir": str(self.profile_dir),
            "startedAtEpochMs": self.started_at_epoch_ms,
        }
        if startup_ms is not None:
            metadata["startupMs"] = max(0, int(startup_ms))
        self._write(metadata)
        return metadata

    def publish_metadata(self, metadata: Dict[str, Any]) -> None:
        payload = dict(metadata)
        payload["runtimeSessionId"] = self.session_id
        payload.setdefault("workerPid", self.worker_pid)
        payload.setdefault("profileDir", str(self.profile_dir))
        payload.setdefault("startedAtEpochMs", self.started_at_epoch_ms)
        self._write(payload)

    def clear(self) -> None:
        # The normal SeleniumBase/CDP shutdown gets the first chance to flush and
        # close Chromium. If Windows still has ARES-owned Chrome descendants
        # alive here, terminate only processes proven to belong to this exact
        # runtime session/profile. Do this before deleting the identity marker so
        # a finished runtime never leaves its profile locked by orphaned Chrome.
        self._terminate_owned_browser_processes()

        target = self.profile_dir / RUNTIME_FILENAME
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if str(raw.get("runtimeSessionId") or "") != self.session_id:
            return
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass

    def _terminate_owned_browser_processes(self) -> None:
        processes: List[psutil.Process] = []
        for pid in self.browser_pids():
            if pid == os.getpid():
                continue
            try:
                process = psutil.Process(pid)
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    processes.append(process)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error):
                continue
        if not processes:
            return

        # Browser.close already had a bounded graceful-flush window in the
        # adapter. Anything still alive now is an orphan of this exact runtime.
        for process in processes:
            try:
                process.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error):
                pass
        _, alive = psutil.wait_procs(processes, timeout=2.0)
        for process in alive:
            try:
                process.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error):
                pass
        if alive:
            psutil.wait_procs(alive, timeout=2.0)

    def _write(self, payload: Dict[str, Any]) -> None:
        target = self.profile_dir / RUNTIME_FILENAME
        temporary = self.profile_dir / f"{RUNTIME_FILENAME}.{self.session_id}.tmp"
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(target)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
