"""Passive CAPTCHA/challenge detection and control discovery via CDP DOM only.

No JavaScript is injected anywhere in this module. Elements are read from the
browser's own DOM tree with ``DOM.getDocument``, ``DOM.querySelectorAll``,
``DOM.describeNode``, ``DOM.getContentQuads`` and ``DOM.getOuterHTML``.

Frame handling: ``DOM.describeNode`` exposes the ``contentDocument`` of an
iframe/frame node, so the walk recurses into same-process frames without
``Runtime.evaluate``. ``DOM.getContentQuads`` already reports coordinates in the
top-level viewport space (verified against a same-origin iframe), therefore no
per-frame offset math is required for CDP clicks.

Target-domain note: challenge iframes become visible in this DOM tree as soon
as the page creates them. Cross-origin frame routing stays with the existing
OOPIF registry, which consumes ``Target.attachedToTarget`` /
``Target.detachedFromTarget`` on its own connection.
"""

from __future__ import annotations

import html as html_module
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import mycdp

MAX_FRAME_DEPTH = 4

ELEMENT_SELECTORS = (
    "#recaptcha-verify-button",
    ".rc-button-default",
    ".rc-imageselect",
    ".cf-turnstile",
)

# Iframe entries need a minimum box so an invisible reCAPTCHA v3 badge does not
# permanently lock the main page.
FRAME_SELECTORS = (
    'iframe[src*="recaptcha" i]',
    'iframe[title*="recaptcha" i]',
    'iframe[src*="hcaptcha" i]',
    'iframe[src*="turnstile" i]',
    'iframe[title*="challenge" i]',
)

_MIN_FRAME_WIDTH = 100.0
_MIN_FRAME_HEIGHT = 50.0

PREFERRED_SUBMIT_SELECTORS = (
    "#recaptcha-verify-button",
    ".rc-button-default",
)

GENERIC_CONTROL_SELECTORS = (
    "button",
    "input[type=submit]",
    "[role=button]",
)

_TAG_RE = re.compile(r"(?is)<[^>]*>")
_ATTR_RE = re.compile(r"""\b(?:value|aria-label)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)


class CdpChallengeObserver:
    """DOM-domain-only challenge probe and control locator (100% JS-free)."""

    def __init__(self, seleniumbase_cdp: Any) -> None:
        self._sb = seleniumbase_cdp

    def present(self) -> bool:
        context = self._context()
        if context is None:
            return False
        tab, loop = context
        try:
            return bool(loop.run_until_complete(self._present(tab)))
        except Exception:
            return False

    def preferred_submit_center(
        self,
        window: Optional[Dict[str, float]] = None,
    ) -> Optional[Tuple[float, float]]:
        """Return the center of a provider-native verify button, if visible."""
        context = self._context()
        if context is None:
            return None
        tab, loop = context
        try:
            return loop.run_until_complete(
                self._find_control(tab, PREFERRED_SUBMIT_SELECTORS, (), window, None)
            )
        except Exception:
            return None

    def confirmation_center(
        self,
        *,
        window: Optional[Dict[str, float]] = None,
        accepted_texts: Sequence[str] = (),
        override_selector: Optional[str] = None,
        grid_in_frame: Optional[bool] = None,
    ) -> Optional[Tuple[float, float]]:
        """Find a confirm/continue control via DOM queries and HTML text parsing.

        Ranking mirrors the previous JS fallback: an explicit override wins,
        then provider-native verify buttons, then text-matched generic controls.
        When ``grid_in_frame`` is set, generic controls are strictly limited to
        the same document level as the grid (puzzle window lock).
        """
        context = self._context()
        if context is None:
            return None
        tab, loop = context
        accepted = tuple(
            _normalize(value)
            for value in accepted_texts
            if _normalize(value)
        )
        override = str(override_selector or "").strip() or None
        try:
            return loop.run_until_complete(
                self._find_control(
                    tab,
                    PREFERRED_SUBMIT_SELECTORS,
                    accepted,
                    window,
                    override,
                    include_generic=True,
                    grid_in_frame=grid_in_frame,
                )
            )
        except Exception:
            return None

    async def _present(self, tab: Any) -> bool:
        doc = await tab.send(mycdp.dom.get_document(-1, True))
        return await self._present_in(tab, doc.node_id, 0)

    async def _present_in(self, tab: Any, doc_node_id: Any, depth: int) -> bool:
        if depth > MAX_FRAME_DEPTH:
            return False
        for selector in ELEMENT_SELECTORS:
            if await self._has_visible(tab, doc_node_id, selector, 0.0, 0.0):
                return True
        for selector in FRAME_SELECTORS:
            if await self._has_visible(tab, doc_node_id, selector, _MIN_FRAME_WIDTH, _MIN_FRAME_HEIGHT):
                return True
        for child_doc_id in await self._child_documents(tab, doc_node_id):
            if await self._present_in(tab, child_doc_id, depth + 1):
                return True
        return False

    async def _find_control(
        self,
        tab: Any,
        preferred_selectors: Sequence[str],
        accepted_texts: Sequence[str],
        window: Optional[Dict[str, float]],
        override_selector: Optional[str],
        *,
        include_generic: bool = False,
        grid_in_frame: Optional[bool] = None,
    ) -> Optional[Tuple[float, float]]:
        doc = await tab.send(mycdp.dom.get_document(-1, True))
        candidates: List[Tuple[int, float, float]] = []
        await self._collect_controls(
            tab,
            doc.node_id,
            preferred_selectors,
            accepted_texts,
            window,
            override_selector,
            include_generic,
            grid_in_frame,
            candidates,
            0,
        )
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return (candidates[0][1], candidates[0][2])

    async def _collect_controls(
        self,
        tab: Any,
        doc_node_id: Any,
        preferred_selectors: Sequence[str],
        accepted_texts: Sequence[str],
        window: Optional[Dict[str, float]],
        override_selector: Optional[str],
        include_generic: bool,
        grid_in_frame: Optional[bool],
        candidates: List[Tuple[int, float, float]],
        depth: int,
    ) -> None:
        if depth > MAX_FRAME_DEPTH:
            return
        in_frame = depth > 0
        if override_selector:
            await self._collect_selector(
                tab, doc_node_id, override_selector, 0, False, accepted_texts, window,
                in_frame, grid_in_frame, candidates,
            )
        for selector in preferred_selectors:
            await self._collect_selector(
                tab, doc_node_id, selector, 1, False, accepted_texts, window,
                in_frame, grid_in_frame, candidates,
            )
        if include_generic and not override_selector:
            for selector in GENERIC_CONTROL_SELECTORS:
                await self._collect_selector(
                    tab, doc_node_id, selector, 2, True, accepted_texts, window,
                    in_frame, grid_in_frame, candidates,
                )
        for child_doc_id in await self._child_documents(tab, doc_node_id):
            await self._collect_controls(
                tab,
                child_doc_id,
                preferred_selectors,
                accepted_texts,
                window,
                override_selector,
                include_generic,
                grid_in_frame,
                candidates,
                depth + 1,
            )

    async def _collect_selector(
        self,
        tab: Any,
        doc_node_id: Any,
        selector: str,
        rank: int,
        match_text: bool,
        accepted_texts: Sequence[str],
        window: Optional[Dict[str, float]],
        in_frame: bool,
        grid_in_frame: Optional[bool],
        candidates: List[Tuple[int, float, float]],
    ) -> None:
        if match_text and grid_in_frame is not None and in_frame != grid_in_frame:
            # Strict puzzle-window lock: generic buttons are only considered in
            # the same document level as the grid (frames vs main document).
            return
        for node_id in await self._node_ids(tab, doc_node_id, selector):
            bounds = await self._bounds(tab, node_id)
            if bounds is None:
                continue
            center = ((bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0)
            if rank != 0 and not _inside(center, window):
                continue
            if match_text:
                if not accepted_texts:
                    continue
                markup = await self._outer_html(tab, node_id)
                if not _text_matches(_control_text(markup), accepted_texts):
                    continue
            candidates.append((rank, center[0], center[1]))

    async def _has_visible(
        self,
        tab: Any,
        doc_node_id: Any,
        selector: str,
        min_width: float,
        min_height: float,
    ) -> bool:
        for node_id in await self._node_ids(tab, doc_node_id, selector):
            bounds = await self._bounds(tab, node_id)
            if bounds is None:
                continue
            if bounds[2] - bounds[0] >= min_width and bounds[3] - bounds[1] >= min_height:
                return True
        return False

    @staticmethod
    async def _node_ids(tab: Any, doc_node_id: Any, selector: str) -> List[Any]:
        try:
            node_ids = await tab.send(mycdp.dom.query_selector_all(doc_node_id, selector))
        except Exception:
            return []
        return list(node_ids or [])

    @staticmethod
    async def _child_documents(tab: Any, doc_node_id: Any) -> List[Any]:
        documents: List[Any] = []
        for node_id in await CdpChallengeObserver._node_ids(tab, doc_node_id, "iframe,frame"):
            try:
                described = await tab.send(mycdp.dom.describe_node(node_id=node_id))
            except Exception:
                continue
            child = getattr(described, "content_document", None)
            child_id = getattr(child, "node_id", None)
            if child_id is not None:
                documents.append(child_id)
        return documents

    @staticmethod
    async def _bounds(tab: Any, node_id: Any) -> Optional[Tuple[float, float, float, float]]:
        try:
            quads = await tab.send(mycdp.dom.get_content_quads(node_id=node_id))
        except Exception:
            return None
        if not quads:
            return None
        values = list(quads[0])
        if len(values) < 8:
            return None
        try:
            xs = [float(values[index]) for index in range(0, 8, 2)]
            ys = [float(values[index]) for index in range(1, 8, 2)]
        except (TypeError, ValueError):
            return None
        return (min(xs), min(ys), max(xs), max(ys))

    @staticmethod
    async def _outer_html(tab: Any, node_id: Any) -> str:
        try:
            return str(await tab.send(mycdp.dom.get_outer_html(node_id=node_id)) or "")
        except Exception:
            return ""

    def _context(self) -> Optional[Tuple[Any, Any]]:
        get_tab = getattr(self._sb, "get_active_tab", None)
        get_loop = getattr(self._sb, "get_event_loop", None)
        if not callable(get_tab) or not callable(get_loop):
            return None
        try:
            tab = get_tab()
            loop = get_loop()
        except Exception:
            return None
        if tab is None or loop is None:
            return None
        return (tab, loop)


class ChallengeWatchdog:
    """Continuous passive challenge poll for the single browser owner loop.

    The watchdog never opens its own CDP connection and never runs on a second
    thread. It is driven by the existing owner loop (worker idle loop, monitor
    scheduler) and therefore stays free of concurrent websocket sends.
    """

    DEFAULT_INTERVAL_SECONDS = 0.3

    def __init__(
        self,
        seleniumbase_cdp: Any,
        *,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        observer: Optional[CdpChallengeObserver] = None,
    ) -> None:
        self._observer = observer or CdpChallengeObserver(seleniumbase_cdp)
        self._interval_seconds = max(0.05, float(interval_seconds))
        self._next_poll_at = 0.0
        self._last_present = False
        self._generation = 0
        self._polls = 0
        self._last_checked_at = 0.0

    def reset(self) -> None:
        """Re-arm for a fresh document (call after navigation)."""
        self._next_poll_at = 0.0
        self._last_present = False

    def poll_if_due(self, *, force: bool = False) -> Dict[str, Any]:
        now = time.monotonic()
        if not force and now < self._next_poll_at:
            return self._state(due=False, changed=False)
        self._next_poll_at = now + self._interval_seconds
        present = bool(self._observer.present())
        changed = present != self._last_present
        if changed:
            self._generation += 1
        self._last_present = present
        self._polls += 1
        self._last_checked_at = now
        return self._state(due=True, changed=changed)

    def present(self) -> bool:
        return self._last_present

    def status(self) -> Dict[str, Any]:
        return {
            "intervalSeconds": self._interval_seconds,
            "present": self._last_present,
            "generation": self._generation,
            "polls": self._polls,
            "lastCheckedAt": self._last_checked_at,
            "nextPollAt": self._next_poll_at,
        }

    def _state(self, *, due: bool, changed: bool) -> Dict[str, Any]:
        return {
            "due": bool(due),
            "present": self._last_present,
            "changed": bool(changed),
            "generation": self._generation,
            "polls": self._polls,
            "checkedAt": self._last_checked_at,
        }


def _inside(point: Tuple[float, float], window: Optional[Dict[str, float]]) -> bool:
    if window is None:
        return True
    return (
        window.get("left", float("-inf")) <= point[0] <= window.get("right", float("inf"))
        and window.get("top", float("-inf")) <= point[1] <= window.get("bottom", float("inf"))
    )


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _text_matches(value: str, accepted: Sequence[str]) -> bool:
    if not value:
        return False
    if value in accepted:
        return True
    for word in accepted:
        if len(word) >= 3 and word in value:
            return True
    return False


def _control_text(markup: str) -> str:
    if not markup:
        return ""
    inner = _TAG_RE.sub(" ", html_module.unescape(markup))
    inner = _normalize(inner)
    if inner:
        return inner
    match = _ATTR_RE.search(markup)
    if match:
        return _normalize(match.group(1) or match.group(2) or "")
    return ""
