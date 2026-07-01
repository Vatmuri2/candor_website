"""
OpenAI-backed version of candor's interviewer.

Upstream candor ran a local Qwen model (transformers/torch). Here we keep
candor's exact system prompt, per-turn "one question only" reminder, and
reply-cleaning filters, but generate the question with an OpenAI chat model so
it deploys without GPUs. Turn-by-turn interface (first_question / answer) makes
it easy to run side by side with the SparkMe interviewer.
"""
import os
from typing import Optional

from openai import OpenAI

from bots.candor.prompts import SYSTEM_PROMPT, clean_interviewer_reply

# Reminder appended to each user turn — verbatim from candor's app.py.
_TURN_REMINDER = (
    "\n[Reminder: one question only. No evaluation, no advice, "
    "no restating their answer, no closing language.]"
)


def _model_name() -> str:
    return (
        os.getenv("CANDOR_MODEL_NAME")
        or os.getenv("MODEL_NAME")
        or "gpt-4.1-mini"
    )


class CandorInterviewer:
    """A single candor interview session, backed by OpenAI."""

    def __init__(self, topic_desc: str, model: Optional[str] = None):
        self.model = model or _model_name()
        self.topic_desc = topic_desc
        self.messages = [
            {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{topic_desc}"},
            {"role": "user", "content": "Start with your first question."},
        ]
        self.transcript = []  # [{role, text, flags}]
        self.stats = {"turns": 0, "affirmation": 0, "closing": 0}
        self._client = OpenAI()

    def _generate(self) -> tuple[str, list]:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=self.messages,
            max_tokens=80,
            temperature=0.7,
        )
        raw = (resp.choices[0].message.content or "").strip()
        question, flags = clean_interviewer_reply(raw)
        for f in flags:
            self.stats[f] += 1
        self.messages.append({"role": "assistant", "content": question})
        self.transcript.append({"role": "interviewer", "text": question, "flags": flags})
        return question, flags

    def first_question(self) -> tuple[str, list]:
        return self._generate()

    def answer(self, user_text: str) -> tuple[str, list]:
        self.stats["turns"] += 1
        self.transcript.append({"role": "respondent", "text": user_text, "flags": []})
        self.messages.append({"role": "user", "content": f"{user_text}{_TURN_REMINDER}"})
        return self._generate()
