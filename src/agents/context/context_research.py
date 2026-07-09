"""Researches background context for an interview topic.

Given a topic (e.g. "recent interest rate hikes"), this agent goes and pulls
relevant, current information off the web and turns it into a neutral briefing
the interviewer can lean on. The briefing is shown to the participant/operator
for approval before the interview starts, and once approved it's cached per
topic so it can be reused across conversations (see utils/storage/context_store).

Retrieval uses OpenAI's web-search-preview chat model. The pinned openai==1.54.0
SDK has no Responses API, so we call chat.completions with the search-preview
model and pass web_search_options through extra_body.
"""
import asyncio
import os
import re
import time
from dataclasses import dataclass, field
from functools import partial
from typing import List, Optional

from src.agents.base_agent import BaseAgent
from src.utils.logger.session_logger import SessionLogger

# Default model that can browse the web. Overridable via env.
DEFAULT_SEARCH_MODEL = os.getenv("CONTEXT_SEARCH_MODEL", "gpt-4o-search-preview")

_URL_RE = re.compile(r"https?://[^\s)\]]+")

_RESEARCH_INSTRUCTIONS = (
    "You are a neutral research assistant preparing a background briefing for an "
    "interviewer who will interview people about the topic below. Search the web for "
    "current, reliable information and write a briefing that a well-informed but "
    "impartial interviewer would want in hand.\n\n"
    "Cover, in plain prose with short headers:\n"
    "1. What the topic is and why it is currently relevant (with recent dates).\n"
    "2. The key facts and most recent developments, with concrete numbers/dates.\n"
    "3. The main competing perspectives or points of contention (represent multiple "
    "sides fairly; do not take a side).\n"
    "4. A few specific angles or questions worth exploring in an interview.\n\n"
    "Rules:\n"
    "- Be factual and even-handed. Do NOT editorialize or advocate.\n"
    "- Prefer recent, authoritative sources.\n"
    "- Keep it under ~450 words.\n"
    "- Do not address the interviewer or the reader; just write the briefing.\n\n"
    "TOPIC: {topic}\n"
)


@dataclass
class ContextResearchResult:
    topic: str
    context: str = ""
    sources: List[str] = field(default_factory=list)
    model: str = ""
    generated_at: float = 0.0
    ok: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "context": self.context,
            "sources": self.sources,
            "model": self.model,
            "generated_at": self.generated_at,
            "ok": self.ok,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ContextResearchResult":
        d = d or {}
        return cls(
            topic=d.get("topic", ""),
            context=d.get("context", ""),
            sources=d.get("sources", []) or [],
            model=d.get("model", ""),
            generated_at=d.get("generated_at", 0.0),
            ok=d.get("ok", False),
            error=d.get("error", ""),
        )


class ContextResearchAgent(BaseAgent):
    """Pulls background context for a topic via web search. One per session."""

    def __init__(self, config: dict = None, interview_session=None):
        BaseAgent.__init__(
            self, name="ContextResearchAgent",
            description="Researches background context for an interview topic via web search.",
            config=config or {},
        )
        self.interview_session = interview_session
        self.search_model = (config or {}).get("model_name", DEFAULT_SEARCH_MODEL)

    def _research_sync(self, topic: str) -> ContextResearchResult:
        """Blocking web-search call. Runs off the event loop via research_topic."""
        result = ContextResearchResult(topic=topic, model=self.search_model,
                                       generated_at=time.time())
        try:
            from openai import OpenAI
        except Exception as e:  # pragma: no cover - openai always present in prod
            result.error = f"openai SDK unavailable: {e}"
            return result

        prompt = _RESEARCH_INSTRUCTIONS.format(topic=topic)
        try:
            client = OpenAI()
            resp = client.chat.completions.create(
                model=self.search_model,
                extra_body={"web_search_options": {}},
                messages=[{"role": "user", "content": prompt}],
            )
            msg = resp.choices[0].message
            result.context = (msg.content or "").strip()
            result.sources = self._extract_sources(msg, result.context)
            result.ok = bool(result.context)
        except Exception as e:
            result.error = str(e)
            SessionLogger.log_to_file(
                "execution_log", f"[CONTEXT_RESEARCH] web search failed: {e}"
            )
        return result

    @staticmethod
    def _extract_sources(msg, text: str) -> List[dict]:
        """Collect {title, url} sources from message annotations, else from the text.

        The search-preview model returns url_citation annotations as plain dicts
        under msg.annotations. If the briefing came back without citations we fall
        back to scraping any URLs out of the prose.
        """
        raw: List[tuple] = []  # (url, title)
        annotations = getattr(msg, "annotations", None) or []
        for ann in annotations:
            cite = ann.get("url_citation") if isinstance(ann, dict) else \
                getattr(ann, "url_citation", None)
            if isinstance(cite, dict):
                url, title = cite.get("url"), cite.get("title")
            elif cite is not None:
                url, title = getattr(cite, "url", None), getattr(cite, "title", None)
            else:
                url, title = None, None
            if url:
                raw.append((url, title))
        if not raw:
            raw = [(u, None) for u in _URL_RE.findall(text or "")]

        seen, out = set(), []
        for url, title in raw:
            url = re.split(r"[?&]utm_source=", url)[0].rstrip(".,);")
            if url in seen:
                continue
            seen.add(url)
            out.append({"url": url, "title": (title or "").strip() or url})
        return out[:12]

    async def research_topic(self, topic: str) -> ContextResearchResult:
        """Research a topic and return a briefing + sources."""
        topic = (topic or "").strip()
        if not topic:
            return ContextResearchResult(topic=topic, error="empty topic",
                                         generated_at=time.time())
        SessionLogger.log_to_file(
            "execution_log", f"[CONTEXT_RESEARCH] researching topic: {topic!r}"
        )
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, partial(self._research_sync, topic))
        SessionLogger.log_to_file(
            "execution_log",
            f"[CONTEXT_RESEARCH] ok={result.ok} sources={len(result.sources)} "
            f"chars={len(result.context)}"
        )
        return result
