"""IntroductionAgent — owns turns 1 (and optionally 2) of the interview.

The rest of the interview is driven by the Interviewer + AgendaManager +
ExplorationPlanner. This agent's whole job is the *opening move*: greet the
respondent, frame what this is, and ask a briefing-aware first question that's
pinned to the deterministic opening subtopic. It then hands the Interviewer a
short **stance note** describing what the opener established (formality, what
the respondent likely already knows about the topic, any cues in the portrait
that should shape follow-ups) so turn 2 doesn't blindly retry the same slot.

Design intent: keep the number of *voices* the respondent hears at exactly one
(the Interviewer). This agent produces the text that the Interviewer speaks on
turn 1. It does not subscribe to messages and does not talk back.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from src.agents.base_agent import BaseAgent
from src.utils.logger.session_logger import SessionLogger


_INTRO_INSTRUCTIONS = """\
You are the OPENING agent of a research interview. You write ONLY the very
first thing the interviewer says to the respondent. Everything after this
opener is handled by another agent, so this must land cleanly and hand off.

## Interview topic
{topic}

## What YOU know going in
User portrait (may be empty on a first contact):
{portrait}

Prior sessions with this person (may be empty):
{last_meeting}

Topic briefing from web research (may be empty). The respondent has NOT said
any of this. Do NOT attribute it to them. Use it to make the opener specific
rather than generic:
{briefing}

## Deterministic opening subtopic
Your FIRST question must be a natural, open-ended phrasing of this subtopic:
    "{opening_subtopic}"
This is fixed. Your job is only to phrase it well and make it land.

## Rules for the opener
1. TWO or THREE sentences total, followed by one question. Nothing more.
2. Sentence 1: short greeting + name the SPECIFIC subject (not "your
   experiences", not "your background" — the actual topic).
3. Sentence 2 (optional): ONE concrete framing detail. Options:
   - a public fact from the briefing that sets the stakes,
   - a piece of the portrait that makes the choice of them-as-respondent
     make sense,
   - a plain note on scope ("I'll be asking about X, Y, and Z").
   Do NOT summarize the whole briefing. One detail, max.
4. Sentence 3: ONE concrete, open-ended question that phrases the opening
   subtopic. Invite a specific story, moment, or account.
5. NEVER:
   - editorialize, flatter, praise, thank, or evaluate;
   - state your opinion or take a side on the topic;
   - ask for PII (name, age, exact address, contact info, IDs);
   - say "welcome back," "picking up where we left off," "we spoke before,"
     or otherwise reveal to the respondent that you have prior notes on them;
   - say "as a researcher I…" or self-disclose beyond "I'm a research
     interviewer studying {topic}".

## Output format
Output BOTH sections, in this exact form, with no extra prose:

<opener>
[The 2–3 sentences of interviewer speech, ending in the first question.]
</opener>

<handoff_note>
[One or two sentences to the NEXT agent (the Interviewer). Name the stance you
set (formal vs. warm), one thing from the portrait or briefing worth watching
for in the respondent's first answer, and — if useful — one probe you would
have asked second but did not. Do NOT re-list the topic; the next agent knows
it. Keep under 60 words. This text is NEVER shown to the respondent.]
</handoff_note>
"""


@dataclass
class OpeningTurn:
    opener_text: str
    handoff_note: str = ""

    def is_usable(self) -> bool:
        return bool(self.opener_text and self.opener_text.strip())


class IntroductionAgent(BaseAgent):
    """Composes the interview's opening turn from topic + briefing + portrait."""

    def __init__(self, config: Optional[dict] = None, interview_session=None):
        cfg = dict(config or {})
        model = os.getenv("INTRODUCTION_MODEL_NAME")
        if model:
            cfg.setdefault("model_name", model)
        BaseAgent.__init__(
            self, name="IntroductionAgent",
            description="Composes the first interviewer turn: framing + first question.",
            config=cfg,
        )
        self.interview_session = interview_session
        self.last_turn: Optional[OpeningTurn] = None

    async def compose_opener(self, topic: str, opening_subtopic: str,
                             portrait: str = "", last_meeting: str = "",
                             briefing: str = "") -> OpeningTurn:
        prompt = _INTRO_INSTRUCTIONS.format(
            topic=topic or "",
            portrait=(portrait or "(none)").strip() or "(none)",
            last_meeting=(last_meeting or "(none)").strip() or "(none)",
            briefing=(briefing or "(none)").strip() or "(none)",
            opening_subtopic=opening_subtopic or "the person's relationship to this topic",
        )
        try:
            raw = (await self.call_engine_async(prompt) or "").strip()
        except Exception as e:
            SessionLogger.log_to_file(
                "execution_log",
                f"[INTRO] compose_opener failed: {e}"
            )
            return OpeningTurn(opener_text="")

        opener_text = _extract_tagged(raw, "opener")
        handoff = _extract_tagged(raw, "handoff_note")

        # If the model forgot the tags, treat the whole thing as opener text —
        # better a working opener than a silent turn.
        if not opener_text:
            opener_text = raw

        turn = OpeningTurn(opener_text=opener_text.strip(), handoff_note=handoff.strip())
        self.last_turn = turn
        SessionLogger.log_to_file(
            "execution_log",
            f"[INTRO] opener={len(turn.opener_text)}c handoff={len(turn.handoff_note)}c"
        )
        return turn


def _extract_tagged(raw: str, tag: str) -> str:
    """Return the inner text of the FIRST <tag>...</tag> block in raw, or ''."""
    import re
    m = re.search(rf"<{tag}>(.*?)</{tag}>", raw or "", re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""
