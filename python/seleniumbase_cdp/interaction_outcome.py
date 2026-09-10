from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List


@dataclass(frozen=True)
class InteractionOutcome:
    stage: str
    kind: str
    success: bool
    verified: bool
    fallback_needed: bool
    reason: str = ""
    details: Dict[str, Any] | None = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["fallbackNeeded"] = data.pop("fallback_needed")
        return data


def summarize_outcomes(outcomes: Iterable[InteractionOutcome]) -> Dict[str, Any]:
    items: List[InteractionOutcome] = list(outcomes)
    verified = all(item.verified for item in items) if items else True
    success = all(item.success for item in items) if items else True
    fallback_needed = any(item.fallback_needed for item in items)
    return {
        "success": success,
        "verified": verified,
        "fallbackNeeded": fallback_needed,
        "outcomes": [item.to_dict() for item in items],
    }


def from_semantic_result(result: Dict[str, Any]) -> Dict[str, Any]:
    outcomes = []
    for item in result.get("results", []) if isinstance(result, dict) else []:
        verified = bool(item.get("verified"))
        outcomes.append(InteractionOutcome(
            stage="verified" if verified else "failed",
            kind="semantic",
            success=verified,
            verified=verified,
            fallback_needed=not verified,
            reason=str(item.get("reason") or ""),
            details=dict(item),
        ))
    return summarize_outcomes(outcomes)


def from_visual_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return summarize_outcomes([
            InteractionOutcome(
                stage="failed",
                kind="visual",
                success=False,
                verified=False,
                fallback_needed=True,
                reason="invalid-visual-result",
            )
        ])

    acted = bool(result.get("acted"))
    verified = bool(result.get("verified", acted))
    reason = str(result.get("reason") or result.get("error") or "")
    return summarize_outcomes([
        InteractionOutcome(
            stage="verified" if verified else ("attempted" if acted else "observed"),
            kind=str(result.get("kind") or "visual"),
            success=acted and verified,
            verified=verified,
            fallback_needed=acted and not verified,
            reason=reason,
            details=dict(result),
        )
    ])
