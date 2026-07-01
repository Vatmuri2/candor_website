"""
Vendored from the candor interview bot (github.com/rdrivers/candor,
interview_bot.py) — the pure-Python interviewer prompt, topic list, and
reply-cleaning filters. The only change vs. upstream is that this file drops
the transformers/torch imports so it can run without local models; question
generation happens via OpenAI in interviewer.py.
"""
import re

# ---------------------------------------------------------------------------
# Interviewer system prompt (verbatim from candor)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are a journalist interviewing a real person. Your only job is to ask questions. "
    "One short question per turn. Nothing else.\n\n"
    "STRICT RULES:\n"
    "- Never react to or evaluate what they said. Do not say things like "
    "'That's understandable', 'Interesting', 'That sounds great', 'Absolutely', "
    "'It's great that...', 'That makes sense', or any other judgment of their answer — "
    "positive, negative, or neutral.\n"
    "- Do not summarize, restate, or paraphrase their answer back to them.\n"
    "- Do not give advice, suggestions, or recommendations.\n"
    "- Never thank them, say goodbye, or use closing language. Do not say things like "
    "'Thank you for sharing', 'You're welcome', 'feel free to reach out', "
    "'Have a great day', 'Don't hesitate to', or anything that sounds like ending the conversation. "
    "The interview continues until you are told to stop — there is no natural end point you "
    "should anticipate.\n"
    "- Start your response directly with the question. No preamble, no opener.\n"
    "- If they ask you a question, ignore it and ask your next question.\n"
    "- If their answer wanders off topic, redirect back to the topic with your next question.\n"
    "- The example questions for this topic are starting points, not a checklist. Once you've "
    "asked them, do not run out of things to ask — instead, pick a specific detail from their "
    "most recent answer and ask a follow-up question that goes deeper on that detail "
    "(ask for a specific number, a specific example, a reason, or what happened last time).\n"
    "- When probing deeper, only use words, concepts, and framing that the respondent has "
    "themselves already used. Do not introduce new interpretive language they didn't say "
    "(for example, if they describe a workout, do not ask about their 'fitness journey', "
    "'pushing past comfort zones', or 'fear of failure' unless they used those words first). "
    "Quote or closely echo their own phrasing back to them in your question rather than "
    "supplying your own framing.\n"
    "- A short neutral acknowledgment like 'Okay.' or 'Got it.' before the question is allowed, "
    "but never an evaluative one."
)

# ---------------------------------------------------------------------------
# Built-in topics (verbatim from candor)
# ---------------------------------------------------------------------------
TOPICS = {
    "1": (
        "Shopping & Spending",
        "Topic: Shopping & Spending. "
        "Find out: how much they spend each month, where their money goes, whether they have debt, "
        "what they buy impulsively, whether they feel in control of their finances. "
        "Example questions: How much do you spend on groceries a week? Do you have credit card debt? "
        "What's the last thing you bought that you regretted? Do you look at your bank balance regularly?",
    ),
    "2": (
        "Health & Fitness",
        "Topic: Health & Fitness. "
        "Find out: how often they actually exercise, what they ate recently, how much they sleep, "
        "what their body image is like, whether they have any health issues they're ignoring. "
        "Example questions: When did you last work out? What did you eat yesterday? "
        "How many hours of sleep do you usually get? Is there anything about your health you're worried about?",
    ),
    "3": (
        "Family & Relationships",
        "Topic: Family & Relationships. "
        "Find out: who they live with, whether they're in a relationship, how close they are to family, "
        "what their family conflicts look like, and how they actually spend time with people they care about. "
        "Example questions: Who do you live with? Are you in a relationship? "
        "When did you last talk to your parents? What's a recent argument you had with someone close to you? "
        "Do you feel close to your family?",
    ),
    "4": (
        "Daily Routine",
        "Topic: Daily Routine. "
        "Find out: exact wake-up time, what the first hour of their day looks like, "
        "how long their commute is, how much time they spend on their phone, and how they wind down. "
        "Example questions: What time do you wake up? What's the first thing you do in the morning? "
        "How long is your commute? How many hours a day are you on your phone?",
    ),
    "5": (
        "Work & Ambition",
        "Topic: Work & Ambition. "
        "Find out: what their job actually is, roughly what they earn, whether they like it, "
        "what they wish they were doing instead, and what's stopping them. "
        "Example questions: What do you do for work? How much do you make? "
        "Are you happy there? What would you rather be doing? What's holding you back?",
    ),
}


def topic_desc_from_text(topic_text: str) -> str:
    """Build candor's topic-description string from an arbitrary topic label.

    Mirrors the format candor uses (upstream generated example questions with
    the local model; we keep the framing without the generated examples so no
    model call is needed just to start)."""
    topic_text = (topic_text or "").strip() or "the respondent's life"
    return (
        f"Topic: {topic_text}. "
        f"Find out specific, real details about the respondent's actual life related to {topic_text}."
    )


# ---------------------------------------------------------------------------
# Reply-cleaning filters (verbatim from candor)
# ---------------------------------------------------------------------------
_AFFIRMATION_OPENERS = re.compile(
    r"^\s*("
    r"that('s| is) (understandable|great|true|correct|interesting|good|nice|"
    r"wonderful|fantastic|amazing|important|helpful|reasonable|fair)"
    r"|absolutely"
    r"|certainly"
    r"|interesting[!.]?"
    r"|sure[!.,]?"
    r"|i see[.,]?"
    r"|indeed"
    r"|that sounds (like )?(a |an )?(great|good|nice|interesting|delightful|efficient|reasonable)[^.!?]*"
    r"|it'?s (great|good|nice|wonderful) that[^.!?]*"
    r"|(working from home|cooking|swimming|playing [a-z]+|that hobby) (is|sounds) [^.!?]*"
    r")"
    r"[.,!]?\s*",
    re.IGNORECASE,
)

_CLOSING_LANGUAGE = re.compile(
    r"^\s*("
    r"thank(s| you) for sharing[^.!?]*"
    r"|you'?re (welcome|very welcome)[^.!?]*"
    r"|(feel free|don'?t hesitate) to[^.!?]*"
    r"|have a (great|good|wonderful|nice) (day|one)[^.!?]*"
    r"|remember,? [^.!?]*"
    r"|take (care|breaks)[^.!?]*"
    r")"
    r"[.,!]?\s*",
    re.IGNORECASE,
)


def strip_affirmation_opener(text):
    """Remove a leading evaluative-affirmation clause, if present."""
    cleaned = _AFFIRMATION_OPENERS.sub("", text, count=1).strip()
    return cleaned if cleaned else text


def strip_closing_language(text):
    """Remove leading closing/service-register clauses (may chain)."""
    cleaned = text
    for _ in range(3):
        new_cleaned = _CLOSING_LANGUAGE.sub("", cleaned, count=1).strip()
        if new_cleaned == cleaned:
            break
        cleaned = new_cleaned
    return cleaned if cleaned else text


def clean_interviewer_reply(text):
    """Apply both filters and report whether either fired (for logging)."""
    flags = []
    after_affirmation = strip_affirmation_opener(text)
    if after_affirmation != text:
        flags.append("affirmation")
    after_closing = strip_closing_language(after_affirmation)
    if after_closing != after_affirmation:
        flags.append("closing")
    return after_closing, flags
