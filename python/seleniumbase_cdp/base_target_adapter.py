"""Generic, pluggable target contract for ARES autonomous flows.

Every concrete target adapter (Pokemon Center, future shops, monitors, manual
browsers) inherits from :class:`BaseTargetAdapter`. The core action surface is
defined once here so new targets/accounts can be attached by configuration
(see :mod:`target_adapter_factory`) without touching the core structure again.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, Optional


class BaseTargetAdapter(ABC):
    """Abstract adapter contract shared by all target sub-modules."""

    target_id: str = "default"
    account_id: Optional[str] = None

    def bind_identity(
        self,
        *,
        target_id: Optional[str] = None,
        account_id: Optional[str] = None,
    ) -> None:
        if target_id:
            self.target_id = str(target_id)
        if account_id:
            self.account_id = str(account_id)

    def runtime_identity(self) -> Dict[str, Any]:
        """Universal identity stamp used by every log record."""
        return {
            "pid": os.getpid(),
            "targetId": self.target_id,
            "accountId": self.account_id,
            "adapter": type(self).__name__,
        }

    def runtime_log_entry(self, event: str, **fields: Any) -> Dict[str, Any]:
        """Uniform log payload for the UI filter (target_id/account_id/pid).

        The transport stays with the caller (stdout RPC, existing task logs) so
        no persistence layer is coupled to target code.
        """
        return {
            "ts": time.time(),
            **self.runtime_identity(),
            "event": str(event),
            **fields,
        }

    @abstractmethod
    def goto(self, url: str) -> None:
        """Navigate the active tab to ``url`` and stabilize the session."""

    @abstractmethod
    def check_challenge(self) -> Dict[str, Any]:
        """Return the current challenge/gate state for this target."""

    @abstractmethod
    def execute_flow(self, flow: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        """Execute a target-agnostic action flow and return its outcome."""
