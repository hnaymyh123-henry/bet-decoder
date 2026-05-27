"""Critic — mechanical validation of evidence briefs (no LLM).

Implements PRD §15 Appendix A.4 rules:
  1. direction matches claim / body_md (keyword heuristic)
  2. scores.recency matches sources[].date (delta-day check)
  3. ≥1 support AND ≥1 refute (balance)
  4. overall_balance consistent with evidence_count

Returns:
  {
    "issues": [{"rule": "1|2|3|4", "severity": "warn|error", "message": "..."}],
    "verdict": "accept" | "review" | "reject"
  }
"""
from __future__ import annotations

from datetime import date, datetime


RECENCY_BUCKETS = [(30, 5), (90, 4), (180, 3), (365, 2)]  # days threshold -> expected score


def _parse_date(s: str) -> date | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError, TypeError):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except (ValueError, TypeError):
                continue
        return None


def _expected_recency(days_ago: int) -> int:
    for threshold, score in RECENCY_BUCKETS:
        if days_ago <= threshold:
            return score
    return 1


def _direction_keyword_score(direction: str, text: str) -> int:
    """Crude semantic check: count direction-aligned cue words.
    Returns positive when text aligns with stated direction, negative when it contradicts.
    """
    t = (text or "").lower()
    support_cues = ["超预期", "高于预期", "强劲", "加速", "扩张", "利好", "beat", "exceed", "above", "outperform", "surge", "rally"]
    refute_cues = ["不及预期", "低于预期", "放缓", "下滑", "miss", "below", "underperform", "decline", "concern", "downgrade", "headwind"]
    s_hits = sum(1 for c in support_cues if c in t)
    r_hits = sum(1 for c in refute_cues if c in t)
    if direction == "support":
        return s_hits - r_hits
    if direction == "refute":
        return r_hits - s_hits
    return 0


def validate_evidence_brief(brief: dict, today: date | None = None) -> dict:
    today = today or date.today()
    issues: list[dict] = []
    items = brief.get("evidence_items") or []

    # Rule 1 & 2: per-item checks
    for i, item in enumerate(items):
        direction = item.get("direction")
        claim = item.get("claim", "")
        body = item.get("body_md", "")
        sources = item.get("sources") or []
        scores = item.get("scores") or {}

        if direction not in ("support", "refute", "neutral"):
            issues.append({"rule": "1", "severity": "error",
                           "message": f"item[{i}]: invalid direction {direction!r}"})
            continue

        if direction != "neutral":
            score = _direction_keyword_score(direction, f"{claim}\n{body}")
            if score < 0:
                issues.append({"rule": "1", "severity": "warn",
                               "message": f"item[{i}] direction={direction} but claim/body keywords skew opposite"})

        # Rule 2: recency vs source dates
        claimed_recency = scores.get("recency")
        if claimed_recency and sources:
            newest = None
            for src in sources:
                d = _parse_date(src.get("date", ""))
                if d and (newest is None or d > newest):
                    newest = d
            if newest:
                days_ago = (today - newest).days
                expected = _expected_recency(max(days_ago, 0))
                if abs(int(claimed_recency) - expected) >= 2:
                    issues.append({"rule": "2", "severity": "warn",
                                   "message": f"item[{i}] recency={claimed_recency} but newest source is {days_ago}d old (expected {expected})"})

    # Rule 3: balance
    support = sum(1 for it in items if it.get("direction") == "support")
    refute = sum(1 for it in items if it.get("direction") == "refute")
    if support == 0 or refute == 0:
        issues.append({"rule": "3", "severity": "error",
                       "message": f"evidence imbalance: support={support}, refute={refute} (both must be >=1)"})

    # Rule 4: overall_balance vs evidence_count
    balance = brief.get("overall_balance")
    count = brief.get("evidence_count") or {}
    declared_support = count.get("support", support)
    declared_refute = count.get("refute", refute)
    expected_balance = _classify_balance(declared_support, declared_refute)
    if balance and balance != expected_balance:
        # allow neighboring buckets (e.g. balanced vs lean_support)
        order = ["bear", "lean_bear", "balanced", "lean_support", "support"]
        try:
            gap = abs(order.index(balance) - order.index(expected_balance))
            if gap >= 2:
                issues.append({"rule": "4", "severity": "warn",
                               "message": f"overall_balance={balance!r} but counts suggest {expected_balance!r} (support={declared_support}, refute={declared_refute})"})
        except ValueError:
            issues.append({"rule": "4", "severity": "warn",
                           "message": f"overall_balance={balance!r} not in known buckets"})

    errors = [i for i in issues if i["severity"] == "error"]
    warns = [i for i in issues if i["severity"] == "warn"]
    if errors:
        verdict = "reject"
    elif len(warns) >= 3:
        verdict = "review"
    else:
        verdict = "accept"

    return {
        "issues": issues,
        "verdict": verdict,
        "counts": {"errors": len(errors), "warnings": len(warns)},
    }


def _classify_balance(support: int, refute: int) -> str:
    total = support + refute
    if total == 0:
        return "balanced"
    ratio = support / total
    if ratio >= 0.8:
        return "support"
    if ratio >= 0.6:
        return "lean_support"
    if ratio >= 0.4:
        return "balanced"
    if ratio >= 0.2:
        return "lean_bear"
    return "bear"
