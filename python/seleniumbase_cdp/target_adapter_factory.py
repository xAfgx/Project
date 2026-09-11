"""Dynamic target adapter factory with pluggable account/proxy lookup.

The task worker only needs a generic ``target_id`` + ``account_id``. This
factory resolves the matching adapter class and account data (proxy, user
agent, profile directory) and returns a ready adapter instance.

Account sources are pluggable: pass any ``account_repository`` implementing
``get_account(target_id, account_id)``. A read-only SQLite repository is
provided but stays schema-agnostic (query + column mapping are configuration),
so the real ARES account/runtime table can be attached without core changes.
"""

from __future__ import annotations

import importlib
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, Type

from base_target_adapter import BaseTargetAdapter


@dataclass(frozen=True)
class TargetAccount:
    """Resolved account context for one target run."""

    target_id: str
    account_id: Optional[str] = None
    proxy: Optional[str] = None
    user_agent: Optional[str] = None
    profile_dir: Optional[str] = None
    language: Optional[str] = None
    timezone: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def adapter_kwargs(self) -> Dict[str, Any]:
        """Only forward account values the adapter actually accepts."""
        resolved: Dict[str, Any] = {}
        if self.proxy:
            resolved["proxy"] = self.proxy
        if self.user_agent:
            resolved["user_agent"] = self.user_agent
        if self.profile_dir:
            resolved["profile_dir"] = self.profile_dir
        if self.language:
            resolved["language"] = self.language
        if self.timezone:
            resolved["timezone"] = self.timezone
        return resolved


class AccountRepository(Protocol):
    def get_account(
        self,
        target_id: str,
        account_id: Optional[str] = None,
    ) -> Optional[TargetAccount]:
        ...


class NullAccountRepository:
    """Fail-closed repository used when no SQLite/account source is configured."""

    def get_account(
        self,
        target_id: str,
        account_id: Optional[str] = None,
    ) -> Optional[TargetAccount]:
        return None


class SqliteAccountRepository:
    """Read-only account lookup with configurable SQL and column mapping."""

    def __init__(
        self,
        database_path: str,
        *,
        query: str = (
            "SELECT account_id, proxy, user_agent, profile_dir, language, timezone "
            "FROM accounts WHERE target_id = ? AND account_id = ? LIMIT 1"
        ),
        columns: Optional[Dict[str, str]] = None,
    ) -> None:
        self._database_path = str(database_path)
        self._query = str(query)
        self._columns = {
            "account_id": "account_id",
            "proxy": "proxy",
            "user_agent": "user_agent",
            "profile_dir": "profile_dir",
            "language": "language",
            "timezone": "timezone",
            **(columns or {}),
        }

    def get_account(
        self,
        target_id: str,
        account_id: Optional[str] = None,
    ) -> Optional[TargetAccount]:
        try:
            connection = sqlite3.connect(f"file:{self._database_path}?mode=ro", uri=True, timeout=5.0)
        except sqlite3.Error:
            return None
        try:
            connection.row_factory = sqlite3.Row
            row = connection.execute(self._query, (str(target_id), str(account_id or ""))).fetchone()
            if row is None:
                return None
            def value(name: str) -> Optional[str]:
                column = self._columns.get(name)
                if not column or column not in row.keys():
                    return None
                raw = row[column]
                return None if raw is None else str(raw)
            return TargetAccount(
                target_id=str(target_id),
                account_id=value("account_id") or account_id,
                proxy=value("proxy"),
                user_agent=value("user_agent"),
                profile_dir=value("profile_dir"),
                language=value("language"),
                timezone=value("timezone"),
            )
        except sqlite3.Error:
            return None
        finally:
            connection.close()


class TargetAdapterFactory:
    """Resolve and instantiate the adapter for a generic target id."""

    _registry: Dict[str, str] = {}
    _default_spec: Optional[str] = None

    @classmethod
    def register(cls, target_id: str, adapter_spec: str, *, default: bool = False) -> None:
        """Register a target. ``adapter_spec`` is ``module:ClassName`` or a path."""
        key = str(target_id or "").strip()
        if not key or ":" not in str(adapter_spec):
            raise ValueError("register() requires target_id and 'module:ClassName'")
        cls._registry[key] = str(adapter_spec)
        if default or cls._default_spec is None:
            cls._default_spec = str(adapter_spec)

    @classmethod
    def registered_targets(cls) -> Dict[str, str]:
        return dict(cls._registry)

    @classmethod
    def get_adapter(
        cls,
        target_id: str,
        sb_instance: Any,
        *,
        account_id: Optional[str] = None,
        account_repository: Optional[AccountRepository] = None,
        **adapter_kwargs: Any,
    ) -> BaseTargetAdapter:
        key = str(target_id or "").strip() or "default"
        repository = account_repository or NullAccountRepository()
        account = repository.get_account(key, account_id)
        spec = cls._registry.get(key) or cls._default_spec
        if not spec:
            raise LookupError(
                f"No target adapter registered for {key!r} and no default configured."
            )
        adapter_cls = _import_adapter(spec)
        resolved: Dict[str, Any] = {}
        if account is not None:
            resolved.update(account.adapter_kwargs())
        resolved.update({k: v for k, v in adapter_kwargs.items() if v is not None})
        adapter = adapter_cls(seleniumbase_cdp=sb_instance, **resolved)
        adapter.bind_identity(target_id=key, account_id=account_id)
        return adapter


def _import_adapter(spec: str) -> Type[BaseTargetAdapter]:
    module_name, _, class_name = spec.partition(":")
    module = importlib.import_module(module_name)
    adapter_cls = getattr(module, class_name, None)
    if not isinstance(adapter_cls, type) or not issubclass(adapter_cls, BaseTargetAdapter):
        raise TypeError(f"Target adapter {spec!r} is not a BaseTargetAdapter subclass.")
    return adapter_cls


def register_default_target() -> None:
    """Make the current ARES control-aware adapter the fallback target."""
    TargetAdapterFactory.register(
        "pokemon-center",
        "control_aware_seleniumbase_adapter:ControlAwareSeleniumBaseCdpAdapter",
        default=True,
    )
