from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import mycdp
import psutil
from seleniumbase import sb_cdp

from base_target_adapter import BaseTargetAdapter
from cdp_challenge_observer import ChallengeWatchdog, challenge_present_fast
from challenge_state_tracker import ChallengeStateTracker
from cdp_fetch_bridge import NetworkAnomalyEliminator, CDPFetchBridge
from cdp_session_recovery import (
    captcha_frame_recovery,
    ensure_live_cdp_session,
    wait_for_document_ready,
)
from instruction_input_runtime import InstructionInputRuntime
from interaction_orchestrator import InteractionOrchestrator
from interaction_outcome import from_semantic_result, from_visual_result
from interaction_policy import InteractionPolicy
from observation_capture import ObservationCapture
from page_observation_watchdog import PageObservationWatchdog
from runtime_identity import BrowserRuntimeIdentity
from semantic_interaction_runtime import SemanticInteractionRuntime
from stealth_protection import StealthProtection
from visual_interaction_runtime import VisualInteractionRuntime
from webrtc_proxy_policy import install_webrtc_proxy_policy


class SeleniumBaseCdpAdapter(BaseTargetAdapter):
    """Single ARES boundary around SeleniumBase Pure CDP / MyCDP."""
    def __init__(
        self,
        *,
        profile_dir: str | Path,
        headless: Optional[bool] = False,
        proxy: str | None = None,
        user_agent: str | None = None,
        site_adapter_overrides: Dict[str, str] | None = None,
        browser_args: Iterable[str] | None = None,
        language: str | None = None,
        timezone: str | None = None,
        target_id: str | None = None,
        account_id: str | None = None,
        monitor_mode: bool = False,
    ) -> None:
        self.bind_identity(target_id=target_id, account_id=account_id)
        self._monitor_mode = bool(monitor_mode)
        self.profile_dir = Path(profile_dir).expanduser().resolve()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._runtime_identity = BrowserRuntimeIdentity.create(self.profile_dir)
        runtime_started = time.monotonic()

        # ============================================================
        # HEADDED MODE ENFORCEMENT
        # ============================================================
        # Chrome MUST run in headed mode with real GPU for:
        # - Authentic Canvas/WebGL fingerprints
        # - WebGPU rendering (browserleaks.com compliance)
        # - Real audio context
        # - DataDome behavioral telemetry
        # Headless mode produces software rendering (SwiftShader/Mesa)
        # which is instantly detected by all anti-bot systems.

        kwargs: Dict[str, Any] = {
            "user_data_dir": str(self.profile_dir),
            "headless": bool(headless),
        }
        if proxy:
            kwargs["proxy"] = proxy
        if user_agent:
            kwargs["agent"] = user_agent
        clean_args = [str(value).strip() for value in (browser_args or []) if str(value).strip()]
        kwargs["browser_args"] = self._runtime_identity.browser_args(clean_args)
        if language:
            kwargs["lang"] = str(language)
        if timezone:
            kwargs["tzone"] = str(timezone)

        # ============================================================
        # Chrome flags (only when NOT headless)
        # ============================================================
        if not headless:
            headed_flags = [
                "--enable-gpu-rasterization",
                "--enable-zero-copy",
                "--force-color-profile=srgb",
                "--disable-features=HeadlessChrome,AutomationControlled",
                "--disable-blink-features=AutomationControlled",
                "--font-render-hinting=medium",
                "--disable-gpu-vsync",
                "--enable-benchmarking",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--enable-webrtc-ip-handling-policy",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            ]
            if kwargs["browser_args"]:
                kwargs["browser_args"] = list(kwargs["browser_args"]) + headed_flags
            else:
                kwargs["browser_args"] = headed_flags

        # ============================================================
        # LAUNCH SELENIUMBASE IN HEADED MODE
        # ============================================================
        try:
            self._sb = sb_cdp.Chrome(**kwargs)
        except Exception:
            self._runtime_identity.clear()
            raise

        # ============================================================
        # STEALTH PROTECTION INJECTION
        # ============================================================
        # Inject deep browser stealth scripts via CDP
        # navigator.webdriver = false (NOT undefined)
        # NO hardware fingerprint spoofing - use authentic Windows GPU
        # ONLY Function.prototype.toString patch for prototype invisibility
        self._stealth = StealthProtection(platform="windows")
        self._inject_stealth()

        # ============================================================
        # NETWORK ANOMALY ELIMINATION
        # ============================================================
        # Route ALL network requests through Chrome's TLS connection
        # Eliminates curl_cffi and ensures JA4 fingerprint consistency
        self._network_eliminator = NetworkAnomalyEliminator(self._sb.driver)
        self._network_eliminator.activate()

        # ============================================================
        # CONTINUE INITIALIZATION
        # ============================================================
        self._runtime_metadata = self._runtime_identity.ready_metadata(
            self,
            startup_ms=(time.monotonic() - runtime_started) * 1000.0,
        )
        self._policy = InteractionPolicy.from_profile(self.profile_dir)
        self._capture = ObservationCapture(self._sb, profile_dir=self.profile_dir, policy=self._policy)
        self._challenge_tracker = ChallengeStateTracker(self._sb)
        self._visual_interactions = VisualInteractionRuntime(
            self._sb,
            profile_dir=self.profile_dir,
            overrides=site_adapter_overrides or {},
            capture=self._capture,
        )
        self._semantic_interactions = SemanticInteractionRuntime(self._sb)
        self._instruction_inputs = InstructionInputRuntime(self._sb)
        self._orchestrator = InteractionOrchestrator()
        self._watchdog = PageObservationWatchdog(self._sb, self._policy)
        self._challenge_watchdog = ChallengeWatchdog(self._sb, interval_seconds=0.3)
        self._next_watchdog_poll = 0.0
        self._last_watchdog_state: Dict[str, Any] = {}
        self._last_auto_result: Dict[str, Any] = {
            "acted": False,
            "kind": "none",
            "outcome": from_visual_result({"acted": False, "kind": "none"}),
        }
        self._last_instruction_result: Dict[str, Any] = {
            "acted": False,
            "verified": False,
            "kind": "instruction-input",
            "reason": "not-run",
        }
        self._last_semantic_outcome: Dict[str, Any] = from_semantic_result({"results": []})
        self._closed = False
        self._initialize_debug_capture()

    @property
    def chrome_pid(self) -> int | None:
        driver = getattr(self._sb, "driver", None)
        if driver is None:
            return None
        if hasattr(driver, "cdp_base"):
            driver = driver.cdp_base
        pid = getattr(driver, "_process_pid", None)
        return int(pid) if isinstance(pid, int) and pid > 0 else None

    def runtime_metadata(self) -> Dict[str, Any]:
        return dict(self._runtime_metadata)

    def _inject_stealth(self) -> None:
        """Inject all stealth JavaScript into the browser context via CDP."""
        try:
            driver = self._sb.driver
            if not driver:
                return

            # Get CDP connection
            if not hasattr(driver, 'cdp'):
                return
            cdp = driver.cdp

            # Inject stealth scripts via Page.addScriptToEvaluateOnNewDocument
            # This ensures ALL new pages and iframes get the stealth patches
            stealth_source = self._stealth.get_all_scripts()

            for script_content in stealth_source:
                try:
                    cdp.execute("Page.addScriptToEvaluateOnNewDocument", {
                        "source": script_content,
                        "runImmediately": True
                    })
                except Exception:
                    pass  # Continue with other scripts even if one fails

            # Inject the Chrome 152 specific patches
            try:
                cdp.execute("Page.addScriptToEvaluateOnNewDocument", {
                    "source": self._stealth.get_script("patchright"),
                    "runImmediately": True
                })
            except Exception:
                pass

            # Install WebRTC proxy policy
            # WebRTC MUST be allowed but must report the same Proxy IP as HTTP
            # This is critical for DataDome cross-layer consistency
            try:
                install_webrtc_proxy_policy(self._sb)
            except Exception:
                pass

            # If we're on an existing page, inject immediately too
            try:
                active_tab = self._sb.get_active_tab()
                if active_tab:
                    cdp.execute("Page.addScriptToEvaluateOnNewDocument", {
                        "source": self._stealth.get_script("core"),
                        "runImmediately": True
                    })
            except Exception:
                pass

        except Exception as e:
            # Stealth injection failure is non-fatal but logged
            pass

    def get_cdp_fetch(self) -> CDPFetchBridge:
        """Get the CDP Fetch bridge for direct network request routing."""
        return CDPFetchBridge(self._sb)

    def api_get(self, url: str) -> Dict:
        """Make a GET request through Chrome's TLS (replaces curl_cffi)."""
        return self._network_eliminator.get(url)

    def api_post(self, url: str, data: Optional[Dict] = None) -> Dict:
        """Make a POST request through Chrome's TLS (replaces curl_cffi)."""
        return self._network_eliminator.post(url, data)

    def install_device_fingerprint(self, seed: int, user_agent: str | None = None) -> bool:
        """Inject the seeded device fingerprint spoof into every new document.

        Used by the manual profile browser so it exposes the same diversified
        hardware/GPU/screen/canvas identity as task-driven sessions. Returns
        True when the script was injected, False otherwise (non-fatal).
        """
        from device_fingerprint_catalog import build_device_fingerprint_script

        script = build_device_fingerprint_script(int(seed) if seed else 1, user_agent)

        # Use SeleniumBase's own tab channel only. Attaching a second flattened
        # session to the root target via the OOPIF registry detaches Chrome's
        # page-level session that SeleniumBase uses - afterwards every
        # evaluate/send on that channel hangs forever, freezing the runtime
        # loop (popup-dismiss) so grid detection never runs.
        try:
            command = getattr(getattr(mycdp, "page", None), "add_script_to_evaluate_on_new_document", None)
            if not callable(command):
                self._fp_diag("inject skipped: mycdp.page.add_script_to_evaluate_on_new_document unavailable")
                return False
            loop = self._sb.get_event_loop()
            tab = self._sb.get_active_tab()
            loop.run_until_complete(tab.send(command(source=script, run_immediately=True)))
            verify = ""
            try:
                verify = str(self._sb.evaluate("typeof window.__aresRawCanvas") or "")
            except Exception as exc:
                verify = f"eval-error:{type(exc).__name__}"
            self._fp_diag(f"inject OK via tab-send seed={int(seed) if seed else 1} verify={verify}")
            return True
        except Exception as exc:
            self._fp_diag(f"inject FAILED: {type(exc).__name__}: {exc}")
            return False

    @staticmethod
    def _fp_diag(message: str) -> None:
        try:
            import tempfile

            path = Path(tempfile.gettempdir()) / "ares-fingerprint-diag.log"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(message + "\n")
        except Exception:
            pass

    def close(self) -> None:
        """Clean up all resources including network eliminator."""
        try:
            if hasattr(self, '_network_eliminator') and self._network_eliminator:
                self._network_eliminator.deactivate()
        except Exception:
            pass
        try:
            self._closed = True
        except Exception:
            pass

    def _profile_browser_pids(self) -> List[int]:
        # Prefer ARES runtime identity over profile-name inference. The UUID is
        # assigned before Chromium starts and inherited by its process family.
        runtime_matches = self._runtime_identity.browser_pids()
        if runtime_matches:
            return runtime_matches

        target = os.path.normcase(str(self.profile_dir))
        matches: List[int] = []
        for process in psutil.process_iter(["pid", "cmdline"]):
            try:
                command_line = [str(value) for value in (process.info.get("cmdline") or [])]
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error):
                continue
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
                if candidate == target:
                    matches.append(int(process.pid))
                    break
        return matches

    def _initialize_debug_capture(self) -> None:
        """Create one reliable session-ready screenshot after CDP is fully usable."""
        deadline = time.monotonic() + 3.0
        attempts = 0
        result: Dict[str, Any] = {
            "captured": False,
            "reason": "capture-not-attempted",
            "event": "session-ready",
            "generation": 0,
        }
        while attempts < 8 and time.monotonic() < deadline:
            attempts += 1
            try:
                active_tab = self._sb.get_active_tab()
            except Exception as exc:
                result = {
                    "captured": False,
                    "reason": "active-tab-unavailable",
                    "error": str(exc),
                    "event": "session-ready",
                    "generation": 0,
                }
                active_tab = None
            if active_tab is not None:
                result = self._capture.capture("session-ready", generation=0, force=True)
                if bool(result.get("captured")):
                    break
            time.sleep(min(0.1 * attempts, 0.4))

        self._record_debug_capture("session-ready", result, attempts=attempts)
        try:
            baseline = self._watchdog.poll()
            self._last_watchdog_state = baseline
        except Exception as exc:
            self._record_debug_error("watchdog-baseline", exc)

    def _record_debug_capture(self, phase: str, result: Dict[str, Any], *, attempts: int | None = None) -> None:
        status: Dict[str, Any] = {
            "phase": str(phase),
            "captured": bool(result.get("captured")),
            "reason": str(result.get("reason") or ""),
            "event": str(result.get("event") or phase),
            "generation": int(result.get("generation") or 0),
        }
        if attempts is not None:
            status["attempts"] = max(0, int(attempts))
        path = str(result.get("path") or "").strip()
        if path:
            status["path"] = path
        error = str(result.get("error") or "").strip()
        if error:
            status["error"] = error[:1000]
        self._runtime_metadata["debugCapture"] = status
        self._runtime_identity.publish_metadata(self._runtime_metadata)

    def _record_debug_error(self, phase: str, error: Exception) -> None:
        self._runtime_metadata["debugCapture"] = {
            "phase": str(phase),
            "captured": False,
            "reason": "runtime-error",
            "error": str(error)[:1000],
        }
        self._runtime_identity.publish_metadata(self._runtime_metadata)

    def _capture_debug(self, event: str, *, generation: int = 0, force: bool = False) -> Dict[str, Any]:
        try:
            result = self._capture.capture(event, generation=generation, force=force)
        except Exception as exc:
            result = {
                "captured": False,
                "reason": "capture-runtime-error",
                "error": str(exc),
                "event": event,
                "generation": generation,
            }
        self._record_debug_capture(event, result)
        return result

    def _enable_focus_emulation(self) -> None:
        """Make the page report `document.hasFocus() === true` at the CDP level.

        The OS window can be foregrounded while Chrome keeps content focus on
        the omnibox or a bubble, so `document.hasFocus()` stays false and
        BrowserScan flags an `unattended-session`. `Emulation.setFocusEmulation-
        Enabled` is a browser-level emulation command: no page JavaScript, no
        property patch, no CDP input, no runtime interaction. It survives
        navigations and is re-asserted after session recovery.
        """
        try:
            if str(os.environ.get("ARES_DISABLE_FOCUS_EMULATION") or "").strip() == "1":
                return
            command = getattr(getattr(mycdp, "emulation", None), "set_focus_emulation_enabled", None)
            if not callable(command):
                self._focus_diag("focus-emulation unavailable")
                return
            tab = self._sb.get_active_tab()
            loop = self._sb.get_event_loop()
            loop.run_until_complete(tab.send(command(enabled=True)))
            self._focus_diag("focus-emulation enabled")
        except Exception as exc:
            self._focus_diag(f"focus-emulation error {type(exc).__name__}: {exc}")

    @staticmethod
    def _focus_diag(message: str) -> None:
        try:
            import tempfile

            path = Path(tempfile.gettempdir()) / "ares-focus-diag.log"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{time.time():.3f} {message}\n")
        except Exception:
            pass

    def goto(self, url: str) -> None:
        self._enable_focus_emulation()
        self._sb.goto(url)
        self._challenge_watchdog.reset()
        ensure_live_cdp_session(self._sb, expected_url=url)
        wait_for_document_ready(self._sb)
        self._challenge_tracker.wait_for_stable_challenge()
        self._captcha_frame_recovery_if_challenge()
        self._visual_interactions.reset_after_navigation()
        self._watchdog.reset()
        initial = self._watchdog.poll()
        self._last_watchdog_state = initial
        self._capture_debug("page-load", generation=int(initial.get("generation") or 0), force=True)
        self._poll_observation_watchdog(force=True)

    def _captcha_frame_recovery_if_challenge(self) -> Dict[str, Any]:
        """Run CAPTCHA recovery only when a challenge is actually present.

        The presence check is pure CDP DOM domain (shallow getDocument +
        querySelector), no JavaScript and no full-document transfer.
        """
        if not challenge_present_fast(self._sb):
            return {"method": "none", "acted": False, "reason": "no-challenge"}
        return captcha_frame_recovery(self._sb)

    def challenge_state(self) -> Dict[str, Any]:
        return self._challenge_tracker.poll()

    def check_challenge(self) -> Dict[str, Any]:
        """BaseTargetAdapter contract: observe the current gate/challenge."""
        return self.challenge_state()

    def site_grid_state(self) -> Dict[str, Any]:
        return self._visual_interactions.grid_state()

    def site_slider_state(self) -> Dict[str, Any]:
        return self._visual_interactions.slider_state()

    def slider_target_state(self) -> Dict[str, Any]:
        return self._visual_interactions.slider_target_state()

    def auto_interaction_state(self) -> Dict[str, Any]:
        return {
            **self._visual_interactions.status(),
            "lastResult": self._last_auto_result,
            "lastInstructionResult": self._last_instruction_result,
            "instructionInputsEnabled": True,
            "orchestrator": self._orchestrator.status(),
            "watchdog": self._watchdog.status(),
            "challengeWatchdog": self._challenge_watchdog.status(),
            "policy": self._policy.status(),
            "capture": self._capture.status(),
        }

    def interaction_outcome_state(self) -> Dict[str, Any]:
        return {
            "enabled": True,
            "mode": "event-or-frame-heartbeat",
            "semantic": self._last_semantic_outcome,
            "visual": self._last_auto_result.get("outcome", from_visual_result(self._last_auto_result)),
            "instructionInput": self._last_instruction_result,
            "orchestrator": self._orchestrator.status(),
            "watchdog": self._last_watchdog_state,
        }

    def observe_semantic_fields(self) -> List[Dict[str, Any]]:
        return self._semantic_interactions.observe_fields()

    def execute_semantic_plan(self, plan: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        items = [dict(item) for item in plan]

        def action() -> Dict[str, Any]:
            result = self._semantic_interactions.execute_plan(items)
            outcome = from_semantic_result(result)
            self._last_semantic_outcome = outcome
            return {**result, "outcome": outcome}

        return self._orchestrator.run_action("semantic", action)

    def execute_flow(self, flow: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        """BaseTargetAdapter contract: run a target-agnostic action flow."""
        return self.execute_semantic_plan(flow)

    def apply_grid_selection(self, indexes: Iterable[int], *, submit: bool = True) -> Dict[str, Any]:
        selected = list(indexes)

        def action() -> Dict[str, Any]:
            result = self._visual_interactions.apply_grid_selection(selected, submit=submit)
            return {**result, "outcome": from_visual_result(result)}

        return self._orchestrator.run_action("grid", action)

    def apply_slider(self, target_fraction: float = 0.96) -> Dict[str, Any]:
        def action() -> Dict[str, Any]:
            result = self._visual_interactions.apply_slider(target_fraction)
            return {**result, "outcome": from_visual_result(result)}

        return self._orchestrator.run_action("slider", action)

    def inspect_session(self) -> Dict[str, Any]:
        return {
            "url": str(self._sb.get_current_url() or ""),
            "title": str(self._sb.get_title() or ""),
            "cookies": self.get_snapshot_cookies(),
            "runtime": self.runtime_metadata(),
            "interactionOutcome": self.interaction_outcome_state(),
        }

    def execute_script(self, script: str, *args: Any) -> Any:
        if not args:
            return self._sb.execute_script(script)
        encoded_args = json.dumps(list(args), ensure_ascii=False, separators=(",", ":"))
        wrapped = (
            "(() => {"
            f"const __aresArgs={encoded_args};"
            "return (function(){"
            f"{script}"
            "}).apply(null,__aresArgs);"
            "})()"
        )
        return self._sb.execute_script(wrapped)

    def sleep(self, seconds: float) -> None:
        self._sb.sleep(seconds)

    def set_snapshot_cookies(self, cookies: Iterable[Dict[str, Any]]) -> int:
        params = [self._cookie_param(dict(cookie)) for cookie in cookies]
        if not params:
            return 0

        driver = getattr(self._sb, "driver", None)
        if driver is not None and hasattr(driver, "cdp_base"):
            driver = driver.cdp_base
        send = getattr(getattr(driver, "connection", None), "send", None)
        if callable(send):
            loop = self._sb.get_event_loop()
            loop.run_until_complete(send(mycdp.storage.set_cookies(params)))
            # Startup cookie injection happens before the first target-domain
            # navigation. Use the browser-scoped Storage domain for readback too,
            # avoiding the not-yet-stable first tab connection on fresh Windows
            # CDP sessions while preserving pre-navigation cookie semantics.
            try:
                loop.run_until_complete(send(mycdp.storage.get_cookies()))
            except Exception:
                pass
        else:
            self._sb.set_all_cookies(params)
            try:
                self._sb.get_all_cookies()
            except Exception:
                pass

        # CDP-injected persistent cookies can become request-visible before
        # Chromium commits them to the profile cookie database. Give Windows a
        # short settle window before an immediate clean close/reopen.
        if sys.platform.startswith("win"):
            time.sleep(1.0)
        return len(params)

    def get_snapshot_cookies(self) -> List[Dict[str, Any]]:
        return [self._cookie_to_snapshot(cookie) for cookie in self._sb.get_all_cookies()]

    def poll_runtime(self) -> None:
        if not self._closed and not self._monitor_mode:
            self.poll_challenge_watchdog()
            self._poll_observation_watchdog()

    def poll_challenge_watchdog(self, *, allow_forced_auto: bool = True) -> Dict[str, Any]:
        """Continuous passive challenge poll (~300 ms) driven by the owner loop.

        Detection runs even when automatic visual work is deferred; the forced
        analysis is only triggered when the flag is allowed and the challenge
        appeared/disappeared since the previous poll.
        """
        state = self._challenge_watchdog.poll_if_due()
        if allow_forced_auto and state.get("due") and (state.get("changed") or state.get("present")):
            self._poll_observation_watchdog(force=True)
        return state

    def challenge_watchdog_state(self) -> Dict[str, Any]:
        return self._challenge_watchdog.status()

    def force_captcha_poll(self) -> bool:
        """Force one observation+auto-interaction cycle regardless of the
        throttle/quiet window. Used after navigation/redirects so a captcha that
        appears only AFTER a redirect is still solved instead of waiting for an
        idle-poll that may never run during an active checkout flow.

        A cheap DOM probe runs first: on pages without a challenge (e.g. the
        storefront during normal checkout) the expensive screenshot/capture +
        orchestrator cycle is skipped, so the synchronous command loop is never
        blocked by heavy visual inference when there is nothing to solve.
        """
        import time as _t
        def _trace(msg: str) -> None:
            print(f"[captcha-poll] {msg} t={_t.monotonic():.1f}", file=sys.stderr, flush=True)
        _trace("start")
        try:
            present = challenge_present_fast(self._sb)
        except Exception:
            present = False
        _trace(f"challenge_present_fast={present}")
        if not present:
            return False
        # Step 1: click the reCAPTCHA checkbox through SeleniumBase's built-in
        # helper (uc_gui_click_rc / solve_captcha). This resolves the common
        # "I'm not a robot" checkbox without the local model or a provider.
        # The helper can block for a long time, so it runs in a bounded thread
        # and is rate-limited to avoid clicking the checkbox twice in a row.
        now = time.monotonic()
        if now - getattr(self, "_last_checkbox_click_at", 0.0) >= 6.0:
            self._last_checkbox_click_at = now
            try:
                import threading
                from cdp_session_recovery import captcha_frame_recovery
                holder: list[Any] = []

                def _click() -> None:
                    try:
                        holder.append(captcha_frame_recovery(self._sb))
                    except Exception as exc:
                        holder.append({"method": "error", "error": str(exc)[:120]})

                worker = threading.Thread(target=_click, daemon=True)
                worker.start()
                worker.join(timeout=6.0)
                if worker.is_alive():
                    _trace("checkbox-click still running (bounded, continuing)")
                else:
                    recovery = holder[0] if holder else {}
                    _trace(f"checkbox-click method={recovery.get('method')} acted={recovery.get('acted')}")
            except Exception:
                _trace("checkbox-click error")
        else:
            _trace("checkbox-click skipped (cooldown)")
        try:
            _t.sleep(1.2)
        except Exception:
            pass
        try:
            still_present = challenge_present_fast(self._sb)
        except Exception:
            still_present = present
        if not still_present:
            _trace("checkbox solved the challenge")
            return True
        # Step 2: API-only mode: skip the local model entirely and let the
        # configured provider solve. Used by monitor, early-gate and Shopify
        # lanes alike.
        mode = "siglip"
        try:
            from captcha_api_provider import captcha_mode
            mode = captcha_mode()
        except Exception:
            mode = "siglip"
        if mode == "api":
            solver = getattr(self._visual_interactions, "_captcha_api_solver", None)
            if solver is not None:
                try:
                    outcome = solver.solve()
                    solved = bool((outcome or {}).get("solved"))
                    _trace(f"api-solve solved={solved} provider={str((outcome or {}).get('provider') or '')}")
                    if solved:
                        return True
                except Exception:
                    _trace("api-solve error")
            _trace("api-only mode: local model skipped")
            return False
        _trace("starting heavy watchdog")
        try:
            self._poll_observation_watchdog(force=True)
            _trace("watchdog done")
            return True
        except Exception:
            _trace("watchdog error")
            return False

    def is_running(self) -> bool:
        if self._closed:
            return False
        pid = self.chrome_pid
        if not pid:
            return True
        try:
            process = psutil.Process(pid)
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False
        except (psutil.AccessDenied, psutil.Error):
            return True

    def _vision_available(self) -> bool:
        vision = getattr(self, "_visual_interactions", None)
        classifier = getattr(vision, "_vision", None) if vision is not None else None
        if classifier is None:
            return True
        if getattr(classifier, "offline", False):
            return False
        status_getter = getattr(classifier, "status", None)
        if callable(status_getter):
            try:
                status = status_getter()
            except Exception:
                return True
            if isinstance(status, dict):
                if status.get("offline"):
                    return False
                if status.get("ready") is False and not status.get("sharedService"):
                    return False
        return True

    def _poll_observation_watchdog(self, *, force: bool = False) -> None:
        if self._monitor_mode and not force:
            # Monitor workers only read the queue DOM via passiveQueueSnapshot.
            # Skip the passive screenshot/capture + orchestrator cycles that can
            # block the synchronous command loop on large pages and starve the
            # `close` command. Explicit solveCaptcha() (force=True) is still
            # honored so a captcha appearing after the queue redirect is solved.
            return
        now = time.monotonic()
        if not force and now < self._next_watchdog_poll:
            return
        self._next_watchdog_poll = now + self._policy.watchdog_interval_seconds
        try:
            state = self._watchdog.poll()
        except Exception as exc:
            self._record_debug_error("watchdog-poll", exc)
            return
        self._last_watchdog_state = state
        events = {str(event) for event in state.get("events") or []}
        action_changed = bool(state.get("changed"))
        visual_changed = bool(events)
        frame_heartbeat = int(state.get("iframes") or 0) > 0

        last = self._last_auto_result if isinstance(self._last_auto_result, dict) else {}
        last_reason = str(last.get("reason") or "")
        last_attempt = int(last.get("attempt") or 0)
        last_max_attempts = int(last.get("maxAttempts") or 3)
        retry_pending = (
            str(last.get("kind") or "") == "image-grid"
            and last.get("verified") is not True
            and last_reason not in {"max-attempts-reached", "explicit-failure"}
            and last_attempt < last_max_attempts
        )

        if not force and not action_changed and not visual_changed and not frame_heartbeat and not retry_pending:
            return
        # NOTE: the offline/vision-unavailable skip lives in
        # AutoInteractionController._handle_grid (grid only). Returning early
        # here would also suppress the pure-DOM slider detection, which never
        # needs the vision model.
        if visual_changed:
            try:
                self._capture_for_events(state)
            except Exception as exc:
                self._record_debug_error("capture-for-events", exc)

        try:
            if force or action_changed:
                self._orchestrator.run_cycle(self._run_visual_auto, self._run_instruction_auto)
            else:
                # Existing iframes need a bounded visual heartbeat because their
                # content can change without mutating the top-level document.
                # Retryable visual results use the same path, so a scheduled retry
                # cannot be lost behind an unchanged main-document fingerprint.
                self._orchestrator.run_action("visual-heartbeat", self._run_visual_auto)
        except Exception:
            pass

    def _capture_for_events(self, state: Dict[str, Any]) -> None:
        generation = int(state.get("generation") or 0)
        events = [str(event) for event in state.get("events") or []]
        priority = (
            "navigation",
            "page-load",
            "iframe-added",
            "modal-opened",
            "grid-candidate",
            "slider-candidate",
            "canvas-candidate",
            "layout-generation-changed",
        )
        forced_events = {"navigation", "page-load", "layout-generation-changed"}
        for event in priority:
            if event not in events:
                continue
            force_capture = event in forced_events
            if force_capture or self._policy.capture_enabled(event):
                self._capture_debug(event, generation=generation, force=force_capture)
                return

    def _run_visual_auto(self) -> Dict[str, Any]:
        try:
            result = self._visual_interactions.poll_and_act()
            self._last_auto_result = {**result, "outcome": from_visual_result(result)}
            if bool(result.get("acted")) and result.get("verified") is False:
                generation = int(self._last_watchdog_state.get("generation") or 0)
                self._capture_debug("verification-failure", generation=generation)
        except Exception as exc:
            result = {"acted": False, "kind": "error", "error": str(exc)}
            self._last_auto_result = {**result, "outcome": from_visual_result(result)}
        return self._last_auto_result

    def _run_instruction_auto(self) -> Dict[str, Any]:
        try:
            self._last_instruction_result = self._instruction_inputs.apply()
        except Exception as exc:
            self._last_instruction_result = {
                "acted": False,
                "verified": False,
                "kind": "instruction-input",
                "reason": "error",
                "error": str(exc),
            }
        return self._last_instruction_result

    def _request_graceful_browser_close(self) -> bool:
        driver = getattr(self._sb, "driver", None)
        if driver is None:
            return False
        if hasattr(driver, "cdp_base"):
            driver = driver.cdp_base
        loop = getattr(self._sb, "loop", None)
        send = getattr(getattr(driver, "connection", None), "send", None)
        if loop is None or not callable(send):
            return False
        try:
            loop.run_until_complete(send(mycdp.browser.close()))
            return True
        except Exception:
            return False

    def quit(self) -> None:
        if self._closed:
            return
        self._closed = True
        browser_pids = self._profile_browser_pids()
        chrome_pid = self.chrome_pid
        if chrome_pid and chrome_pid not in browser_pids:
            browser_pids.insert(0, chrome_pid)
        try:
            try:
                self._sb.get_all_cookies()
            except Exception:
                pass
            # SeleniumBase's underlying Pure-CDP stop path can terminate Chromium
            # directly. Ask Chromium itself to close first so Windows can commit the
            # profile cookie database and local storage before any hard fallback.
            time.sleep(1.0 if sys.platform.startswith("win") else 0.2)
            graceful_close = self._request_graceful_browser_close()
            if graceful_close:
                self._wait_for_profile_flush(browser_pids)
            else:
                self._sb.quit()
                self._wait_for_profile_flush(browser_pids)
        finally:
            self._terminate_remaining_browser_pids(browser_pids)
            self._runtime_identity.clear()

    @staticmethod
    def _wait_for_profile_flush(browser_pids: Iterable[int]) -> None:
        pids = list(dict.fromkeys(int(pid) for pid in browser_pids if isinstance(pid, int) and pid > 0))
        if not pids:
            time.sleep(1.0)
            return
        chrome_pid = pids[0]
        try:
            psutil.Process(chrome_pid).wait(timeout=5.0)
        except psutil.NoSuchProcess:
            pass
        except (psutil.TimeoutExpired, psutil.AccessDenied, psutil.Error):
            pass
        deadline = time.monotonic() + 5.0
        for pid in pids[1:]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                psutil.Process(pid).wait(timeout=remaining)
            except psutil.NoSuchProcess:
                pass
            except (psutil.TimeoutExpired, psutil.AccessDenied, psutil.Error):
                continue
        time.sleep(0.25)

    @staticmethod
    def _terminate_remaining_browser_pids(browser_pids: Iterable[int]) -> None:
        """Terminate only session-owned browser PIDs after shutdown has completed."""
        processes: List[psutil.Process] = []
        for pid in dict.fromkeys(int(pid) for pid in browser_pids if isinstance(pid, int) and pid > 0):
            try:
                process = psutil.Process(pid)
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    processes.append(process)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error):
                continue

        for process in processes:
            try:
                process.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error):
                pass

        _, alive = psutil.wait_procs(processes, timeout=2.0) if processes else ([], [])
        for process in alive:
            try:
                process.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error):
                pass

        if alive:
            psutil.wait_procs(alive, timeout=2.0)

    @staticmethod
    def _cookie_param(cookie: Dict[str, Any]) -> mycdp.network.CookieParam:
        partition_key = cookie.get("partitionKey")
        if partition_key:
            raise ValueError(
                "Partitionierte Cookies werden im SeleniumBase-Testpfad nicht stillschweigend umgeschrieben. "
                "Bitte Snapshot ohne partitionKey verwenden."
            )
        same_site = str(cookie.get("sameSite") or "Lax")
        if same_site not in {"Strict", "Lax", "None"}:
            same_site = "Lax"
        payload: Dict[str, Any] = {
            "name": str(cookie.get("name") or ""),
            "value": str(cookie.get("value") or ""),
            "domain": str(cookie.get("domain") or ""),
            "path": str(cookie.get("path") or "/"),
            "secure": bool(cookie.get("secure")),
            "httpOnly": bool(cookie.get("httpOnly")),
            "sameSite": same_site,
        }
        expires = cookie.get("expires")
        try:
            expires_number = float(expires)
        except (TypeError, ValueError):
            expires_number = -1
        if expires_number > 0:
            payload["expires"] = expires_number
        if not payload["name"] or not payload["domain"]:
            raise ValueError("Cookie ohne Name oder Domain kann nicht in SeleniumBase geladen werden.")
        return mycdp.network.CookieParam.from_json(payload)

    @staticmethod
    def _cookie_to_snapshot(cookie: Any) -> Dict[str, Any]:
        if hasattr(cookie, "to_json"):
            raw = cookie.to_json()
        elif isinstance(cookie, dict):
            raw = dict(cookie)
        else:
            raw = {
                key: getattr(cookie, key)
                for key in dir(cookie)
                if not key.startswith("_") and not callable(getattr(cookie, key, None))
            }
        same_site = raw.get("sameSite") or raw.get("same_site") or "Lax"
        if hasattr(same_site, "to_json"):
            same_site = same_site.to_json()
        same_site = str(same_site)
        if same_site not in {"Strict", "Lax", "None"}:
            same_site = "Lax"
        expires = raw.get("expires", -1)
        if hasattr(expires, "to_json"):
            expires = expires.to_json()
        try:
            expires_number = float(expires)
        except (TypeError, ValueError):
            expires_number = -1
        snapshot: Dict[str, Any] = {
            "name": str(raw.get("name") or ""),
            "value": str(raw.get("value") or ""),
            "domain": str(raw.get("domain") or ""),
            "path": str(raw.get("path") or "/"),
            "expires": expires_number,
            "httpOnly": bool(raw.get("httpOnly", raw.get("http_only", False))),
            "secure": bool(raw.get("secure", False)),
            "sameSite": same_site,
        }
        partition_key = raw.get("partitionKey") or raw.get("partition_key")
        if isinstance(partition_key, str) and partition_key.strip():
            snapshot["partitionKey"] = partition_key.strip()
        elif isinstance(partition_key, dict) and partition_key.get("topLevelSite"):
            snapshot["partitionKey"] = str(partition_key["topLevelSite"])
        return snapshot
