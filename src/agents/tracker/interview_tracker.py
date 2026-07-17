"""InterviewTracker — process-aware supervisor of the *narrative* state.

Separate concerns:
- AgendaManager owns *coverage*: which plan-defined subtopics have notes.
- ExplorationPlanner owns *rollouts*: what path would maximize utility.
- InterviewTracker owns *narrative*: what the respondent has actually claimed,
  what threads are alive vs. dropped, where they contradict themselves, and how
  the interview is landing on them.

Every N turns (default 2) the tracker digests the last few exchanges and updates
a small structured object (NarrativeState). The Interviewer prompt gets a short
"state of the interview" preamble derived from it; the CloserAgent 2.0 consults
`loose_ends` when choosing what to name in the wind-down.

Design notes:
- Cheap: one LLM call per update, JSON out; the state is capped in size (max
  8 threads, 8 loose_ends, 4 contradictions) so it stays under ~500 tokens.
- Robust: on parse failure, keep the previous state and log; never crash the
  interview.
- Save/restore friendly: NarrativeState is a plain dataclass with a to/from-dict
  pair so it can go through the session state serializer.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

from src.agents.base_agent import BaseAgent
from src.utils.logger.session_logger import SessionLogger


# Cap sizes to keep the preamble small.
MAX_THREADS = 8
MAX_LOOSE_ENDS = 8
MAX_CONTRADICTIONS = 4
MAX_UNUSED_BRIEFING = 8


@dataclass
class NarrativeState:
    """Rolling structured state of the interview."""
    live_threads: List[dict] = field(default_factory=list)     # {claim, evidence, opened_at}
    loose_ends: List[dict] = field(default_factory=list)       # {thread, why_worth_pulling}
    contradictions: List[dict] = field(default_factory=list)   # {a, b, turns:[i,j]}
    respondent_stance: dict = field(default_factory=lambda: {
        "cooperativeness": 0.5, "specificity": 0.5, "reserve": 0.5,
    })
    briefing_alignment: dict = field(default_factory=lambda: {
        "used_briefing_facts": [], "unused_briefing_facts": [],
    })
    last_updated_turn: int = 0

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "NarrativeState":
        d = d or {}
        return cls(
            live_threads=(d.get("live_threads") or [])[:MAX_THREADS],
            loose_ends=(d.get("loose_ends") or [])[:MAX_LOOSE_ENDS],
            contradictions=(d.get("contradictions") or [])[:MAX_CONTRADICTIONS],
            respondent_stance=(d.get("respondent_stance") or {
                "cooperativeness": 0.5, "specificity": 0.5, "reserve": 0.5,
            }),
            briefing_alignment=(d.get("briefing_alignment") or {
                "used_briefing_facts": [], "unused_briefing_facts": [],
            }),
            last_updated_turn=int(d.get("last_updated_turn") or 0),
        )

    def to_dict(self) -> dict:
        return {
            "live_threads": self.live_threads,
            "loose_ends": self.loose_ends,
            "contradictions": self.contradictions,
            "respondent_stance": self.respondent_stance,
            "briefing_alignment": self.briefing_alignment,
            "last_updated_turn": self.last_updated_turn,
        }

    def is_empty(self) -> bool:
        return (not self.live_threads and not self.loose_ends
                and not self.contradictions and self.last_updated_turn == 0)

    def preamble_for_interviewer(self) -> str:
        """~300-token summary of narrative state, safe to inject into a prompt."""
        if self.is_empty():
            return ""
        parts = ["<interview_state>"]
        if self.live_threads:
            parts.append("Live threads (things the respondent has actually said):")
            for t in self.live_threads[:MAX_THREADS]:
                claim = (t.get("claim") or "").strip()
                ev = t.get("evidence") or "unknown"
                opened = t.get("opened_at")
                parts.append(f"  - [{ev}, opened@turn {opened}] {claim}")
        if self.loose_ends:
            parts.append("\nLoose ends worth returning to:")
            for le in self.loose_ends[:MAX_LOOSE_ENDS]:
                thread = (le.get("thread") or "").strip()
                why = (le.get("why_worth_pulling") or "").strip()
                parts.append(f"  - {thread} — {why}")
        if self.contradictions:
            parts.append("\nApparent contradictions to probe carefully:")
            for c in self.contradictions[:MAX_CONTRADICTIONS]:
                a = (c.get("a") or "").strip()
                b = (c.get("b") or "").strip()
                turns = c.get("turns") or []
                parts.append(f"  - '{a}' vs. '{b}' (turns {turns})")
        stance = self.respondent_stance or {}
        parts.append(
            "\nRespondent stance (0–1): "
            f"cooperativeness={stance.get('cooperativeness', 0.5):.2f}, "
            f"specificity={stance.get('specificity', 0.5):.2f}, "
            f"reserve={stance.get('reserve', 0.5):.2f}"
        )
        unused = (self.briefing_alignment or {}).get("unused_briefing_facts") or []
        if unused:
            parts.append("\nBriefing facts NOT yet raised in questions:")
            for u in unused[:MAX_UNUSED_BRIEFING]:
                parts.append(f"  - {u}")
        parts.append("</interview_state>")
        return "\n".join(parts)


_TRACKER_PROMPT = """\
You are the InterviewTracker: a silent supervisor that maintains a structured
map of the interview as it happens. You never speak to the respondent. You only
produce an updated JSON state.

## Interview topic
{topic}

## Topic briefing (facts the interviewer knows; respondent did NOT say these)
{briefing}

## Previous narrative state (may be empty on first update)
{prior_state_json}

## Recent conversation (most recent last; "I:" = interviewer, "R:" = respondent)
{recent_dialog}

## Your job
Return an UPDATED narrative state as a JSON object with these keys:

- "live_threads": array of objects {{"claim": str, "evidence": "strong"|"weak", "opened_at": int}}.
  A thread is something the respondent has substantively said. Merge duplicates;
  drop threads that were dropped without answer. Cap at {max_threads}.

- "loose_ends": array of objects {{"thread": str, "why_worth_pulling": str}}.
  Things the respondent hinted at but the interview never followed up on, OR
  briefing facts the respondent hasn't been asked about at all. Cap at {max_loose}.

- "contradictions": array of objects {{"a": str, "b": str, "turns": [int, int]}}.
  Only include a contradiction if it is genuinely inconsistent, not merely
  nuance. Cap at {max_contra}.

- "respondent_stance": object {{"cooperativeness": 0..1, "specificity": 0..1, "reserve": 0..1}}.
  Ordinal judgments about the respondent so far:
    * cooperativeness: are they engaging with questions, or deflecting?
    * specificity: are they giving concrete examples, or generalities?
    * reserve: are they holding back or open?
  Adjust cautiously — small deltas from the prior state, not swings.

- "briefing_alignment": object {{"used_briefing_facts": [str, ...], "unused_briefing_facts": [str, ...]}}.
  Which discrete facts from the briefing (paraphrased short) have been raised in
  the interviewer's questions vs. which have not. Cap unused at {max_unused}.

## Output
Return ONLY a single JSON object. No preamble, no explanation, no code fences.
"""


class InterviewTracker(BaseAgent):
    """Maintains the NarrativeState and offers a compact preamble for other agents."""

    def __init__(self, config: Optional[dict] = None, interview_session=None):
        cfg = dict(config or {})
        model = os.getenv("INTERVIEW_TRACKER_MODEL_NAME")
        if model:
            cfg.setdefault("model_name", model)
        BaseAgent.__init__(
            self, name="InterviewTracker",
            description="Maintains the narrative state of the interview.",
            config=cfg,
        )
        self.interview_session = interview_session
        self.state: NarrativeState = NarrativeState()
        self.update_every = int(os.getenv("INTERVIEW_TRACKER_UPDATE_EVERY", "2"))
        self.max_recent_lines = int(os.getenv("INTERVIEW_TRACKER_MAX_LINES", "10"))
        self._in_flight = False  # coarse re-entrancy guard

    def snapshot(self) -> dict:
        return self.state.to_dict()

    def load_snapshot(self, data: Optional[dict]) -> None:
        self.state = NarrativeState.from_dict(data)

    def preamble(self) -> str:
        return self.state.preamble_for_interviewer()

    def loose_ends(self) -> List[dict]:
        return list(self.state.loose_ends)

    def should_update(self, current_turn: int) -> bool:
        if self._in_flight:
            return False
        if current_turn <= 0:
            return False
        return (current_turn - self.state.last_updated_turn) >= self.update_every

    async def maybe_update(self, current_turn: int,
                           topic: str,
                           briefing: str,
                           recent_dialog: str) -> None:
        """Update the narrative state if enough turns have elapsed. Silent on error."""
        if not self.should_update(current_turn):
            return
        self._in_flight = True
        try:
            prompt = _TRACKER_PROMPT.format(
                topic=topic or "",
                briefing=(briefing or "(none)").strip() or "(none)",
                prior_state_json=json.dumps(self.state.to_dict(), indent=2),
                recent_dialog=(recent_dialog or "").strip() or "(no dialog yet)",
                max_threads=MAX_THREADS,
                max_loose=MAX_LOOSE_ENDS,
                max_contra=MAX_CONTRADICTIONS,
                max_unused=MAX_UNUSED_BRIEFING,
            )
            raw = (await self.call_engine_async(prompt) or "").strip()
        except Exception as e:
            SessionLogger.log_to_file("execution_log", f"[TRACKER] LLM call failed: {e}")
            self._in_flight = False
            return

        parsed = _extract_json_object(raw)
        if not parsed:
            SessionLogger.log_to_file(
                "execution_log",
                f"[TRACKER] Could not parse JSON; keeping previous state. Raw head: {raw[:200]!r}"
            )
            self._in_flight = False
            return

        new_state = NarrativeState.from_dict(parsed)
        new_state.last_updated_turn = current_turn
        self.state = new_state
        SessionLogger.log_to_file(
            "execution_log",
            f"[TRACKER] updated@turn {current_turn}: "
            f"threads={len(new_state.live_threads)} "
            f"loose_ends={len(new_state.loose_ends)} "
            f"contradictions={len(new_state.contradictions)}"
        )
        self._in_flight = False


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


def _extract_json_object(raw: str) -> Optional[dict]:
    """Try hard to pull a single JSON object out of a model reply."""
    if not raw:
        return None
    for chunk in _candidate_chunks(raw):
        try:
            obj = json.loads(chunk)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return None


def _candidate_chunks(raw: str):
    yield raw
    fenced = _JSON_FENCE_RE.search(raw)
    if fenced:
        yield fenced.group(1).strip()
    # First { ... last }
    lb = raw.find("{")
    rb = raw.rfind("}")
    if lb != -1 and rb != -1 and rb > lb:
        yield raw[lb: rb + 1]
