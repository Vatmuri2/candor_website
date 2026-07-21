"""Flags a *streak* of interviewer turns that never push for depth (a reason,
a concrete example, a number, or a contrasting case) — the "no depth-probing"
failure mode named in SPEC.md: "never 'why', never a concrete example, never
a number, never a contrasting case."

Rule-based (regex only, no LLM call, no per-turn word-overlap judgment) — same
cheap, one-counter shape as EngagementMonitor's disengagement streak, so it
slots into the same stats surfacing the admin panel already does for the
other monitors.

Deliberately does NOT judge a single turn as "shallow" on its own: an early
version scored turns against token-overlap with the respondent's prior
answer, and it false-flagged exactly the turns the system is designed to
produce well — Rule 1 probes that legitimately reuse the respondent's own
concrete nouns (see interviewer/prompts.py's "use the respondent's OWN words
and framing" instruction) score as high overlap despite being good, specific
follow-ups. Depth-cue presence, tracked as a streak, doesn't have that
failure mode: it never penalizes reusing the respondent's words, only the
absence of any why/example/number/contrast push over several turns running.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional

# The four depth-probe categories SPEC.md names explicitly.
_REASON_CUE = re.compile(
    r"\bwhy\b|what (led|caused|prompted|made)\b|how come\b", re.IGNORECASE)
_EXAMPLE_CUE = re.compile(
    r"\b(an? (specific )?example|for instance|walk me through|"
    r"describe a( specific)? (time|moment|instance|case)|give an example|"
    r"a moment when|a case where|what exactly|tell me about a time|"
    r"specifically (what|which|who|when|where|how))\b",
    re.IGNORECASE)
_NUMBER_CUE = re.compile(
    r"\bhow (many|much|long|often)\b|what (percentage|number|proportion)\b",
    re.IGNORECASE)
_CONTRAST_CUE = re.compile(
    r"\bcompare(d)?\b|versus\b|\bvs\.?\b|difference between\b|instead of\b|"
    r"rather than\b|contrast(ed|ing)?\b|what changed\b", re.IGNORECASE)

# How many consecutive turns with no depth cue before we flag the streak.
FLAT_STREAK_THRESHOLD = 4


def _depth_cue(question: str) -> Optional[str]:
    """Which depth-probe category (if any) this question pushes for."""
    if _REASON_CUE.search(question):
        return "reason"
    if _EXAMPLE_CUE.search(question):
        return "example"
    if _NUMBER_CUE.search(question):
        return "number"
    if _CONTRAST_CUE.search(question):
        return "contrast"
    return None


@dataclass
class ProbeSignal:
    depth_cue: Optional[str]      # "reason" | "example" | "number" | "contrast" | None
    flat_streak: int              # consecutive turns (including this one) with no cue
    flat_flagged: bool            # True the turn the streak crosses the threshold


class ProbeQualityMonitor:
    """Per-turn depth-probing signal. Never talks to the respondent; the
    Interviewer/admin panel read `stats`/`signals` after the fact."""

    def __init__(self):
        self.signals: List[ProbeSignal] = []
        self.flat_streak = 0
        self.stats = {"turns_scored": 0, "has_depth_cue": 0, "flat_streaks_flagged": 0}

    def observe(self, interviewer_question: str) -> ProbeSignal:
        """Score one finalized interviewer question. Call once per turn, after
        guardrails have produced the text actually sent to the respondent."""
        cue = _depth_cue(interviewer_question or "")

        self.flat_streak = 0 if cue else self.flat_streak + 1
        flagged = self.flat_streak == FLAT_STREAK_THRESHOLD

        signal = ProbeSignal(depth_cue=cue, flat_streak=self.flat_streak, flat_flagged=flagged)
        self.signals.append(signal)
        self.stats["turns_scored"] += 1
        if cue:
            self.stats["has_depth_cue"] += 1
        if flagged:
            self.stats["flat_streaks_flagged"] += 1
        return signal

    def to_state(self) -> dict:
        return {
            "stats": dict(self.stats),
            "flat_streak": self.flat_streak,
            "signals": [s.__dict__ for s in self.signals],
        }

    def load_state(self, data: Optional[dict]) -> None:
        data = data or {}
        self.stats = data.get("stats") or {"turns_scored": 0, "has_depth_cue": 0, "flat_streaks_flagged": 0}
        self.flat_streak = data.get("flat_streak", 0)
        self.signals = [ProbeSignal(**s) for s in data.get("signals", [])]
