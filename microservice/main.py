"""
TODO:
- Add API key auth on /linkedin before public exposure.
- Add per-client rate limiting.
- Replace sequential prompts with full AutoGen group chat.
- Move run history from n8n CSV to a database.
"""
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import OpenAI, OpenAIError
from pydantic import BaseModel, Field

load_dotenv()

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TIMEOUT_SECONDS = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "45"))
MOCK_LLM = os.getenv("MOCK_LLM", "false").lower() in {"1", "true", "yes"}
MAX_HASHTAGS = 5
FORBIDDEN_CONFIDENCE_CAP = 0.3
AGENT_NAMES = ["ideation", "drafting", "hashtags", "critic"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("linkedin-autogen")

app = FastAPI(title="LinkedIn AutoGen-style Microservice", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Brand(BaseModel):
    name: str = Field(min_length=1)
    voice: str = "authoritative, clear, data-led"
    tone: str = "professional"
    forbidden_topics: List[str] = Field(default_factory=list)
    cta_style: str = "soft invitation to discuss"


class Context(BaseModel):
    topic_hints: List[str] = Field(default_factory=list)
    audience: str = "fintech professionals"
    campaign: Optional[str] = None
    product_focus: Optional[str] = None


class Options(BaseModel):
    idea_count: int = Field(default=3, ge=1, le=10)
    min_confidence: float = Field(default=0.7, ge=0.0, le=1.0)


class LinkedInRequest(BaseModel):
    brand: Brand
    context: Context = Field(default_factory=Context)
    options: Options = Field(default_factory=Options)


class Meta(BaseModel):
    model: str
    started_at: str
    finished_at: str
    duration_ms: int
    agents: List[str]
    critic_reason: str
    flags: List[str]
    meets_threshold: bool


class LinkedInResponse(BaseModel):
    ideas: List[str]
    draft: str
    confidence: float
    hashtags: List[str]
    meta: Meta


class ServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def error_body(code: str, message: str, details: Optional[list] = None) -> Dict[str, Any]:
    """Build structured error payload."""
    body: Dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return body


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    """Map service errors to JSON responses."""
    return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.message))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return HTTP 400 for invalid request bodies."""
    details = [{"loc": [str(part) for part in err["loc"]], "msg": err["msg"]} for err in exc.errors()]
    return JSONResponse(
        status_code=400,
        content=error_body("validation_error", "Request body failed validation.", details),
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Hide internals on unexpected failures."""
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(status_code=500, content=error_body("internal_error", "Unexpected server error."))


def get_client() -> OpenAI:
    """Build OpenAI client from environment key."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ServiceError(500, "config_error", "OPENAI_API_KEY is not set.")
    return OpenAI(api_key=api_key, timeout=TIMEOUT_SECONDS)


def run_agent(client: OpenAI, name: str, system_prompt: str, user_prompt: str, temperature: float = 0.7) -> Dict[str, Any]:
    """Call model once and parse the JSON reply."""
    try:
        completion = client.chat.completions.create(
            model=MODEL,
            temperature=temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
    except OpenAIError as exc:
        logger.error("Agent %s upstream failure: %s", name, exc)
        raise ServiceError(502, "upstream_llm_error", f"{name} agent failed: {exc}") from exc

    content = completion.choices[0].message.content or ""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.error("Agent %s returned invalid JSON.", name)
        raise ServiceError(502, "upstream_bad_json", f"{name} agent returned invalid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ServiceError(502, "upstream_bad_json", f"{name} agent returned non-object JSON.")
    logger.info("Agent %s finished.", name)
    return parsed


def brand_brief(req: LinkedInRequest) -> str:
    """Render brand and context as prompt text."""
    brand, ctx = req.brand, req.context
    lines = [
        f"Brand: {brand.name}",
        f"Voice: {brand.voice}",
        f"Tone: {brand.tone}",
        f"Forbidden topics: {', '.join(brand.forbidden_topics) or 'none'}",
        f"CTA style: {brand.cta_style}",
        f"Audience: {ctx.audience}",
        f"Campaign: {ctx.campaign or 'none'}",
        f"Product focus: {ctx.product_focus or 'none'}",
        f"Topic hints: {', '.join(ctx.topic_hints) or 'none'}",
    ]
    return "\n".join(lines)


def ideation_agent(client: OpenAI, brief: str, count: int) -> List[str]:
    """Agent 1: propose distinct LinkedIn post ideas."""
    system = (
        "You are the Ideation Agent for a fintech LinkedIn content team. "
        'Reply with JSON only: {"ideas": ["..."]}.'
    )
    user = (
        f"{brief}\n\nReturn exactly {count} distinct post ideas. "
        "One sentence each. Respect forbidden topics. No investment advice."
    )
    reply = run_agent(client, "ideation", system, user, temperature=0.9)
    raw = reply.get("ideas")
    ideas = [str(item).strip() for item in raw if str(item).strip()] if isinstance(raw, list) else []
    if not ideas:
        raise ServiceError(502, "upstream_bad_json", "ideation agent returned no ideas.")
    return ideas[:count]


def drafting_agent(client: OpenAI, brief: str, ideas: List[str]) -> str:
    """Agent 2: pick best idea and write the post."""
    system = (
        "You are the Drafting Agent for a fintech LinkedIn content team. "
        'Reply with JSON only: {"draft": "..."}.'
    )
    numbered = "\n".join(f"{i + 1}. {idea}" for i, idea in enumerate(ideas))
    user = (
        f"{brief}\n\nIdeas:\n{numbered}\n\n"
        "Pick the strongest idea and write one LinkedIn post. "
        "Strong hook on line one. Maximum 1300 characters. "
        "No hashtags in the text. Follow the CTA style. "
        "Avoid forbidden topics, guaranteed returns, and unverifiable statistics."
    )
    reply = run_agent(client, "drafting", system, user, temperature=0.7)
    draft = str(reply.get("draft", "")).strip()
    if not draft:
        raise ServiceError(502, "upstream_bad_json", "drafting agent returned empty draft.")
    return draft


def normalise_hashtags(raw: Any) -> List[str]:
    """Clean, dedupe, and cap hashtags."""
    if not isinstance(raw, list):
        return []
    seen = set()
    result: List[str] = []
    for item in raw:
        word = re.sub(r"\W", "", str(item))
        if not word:
            continue
        tag = f"#{word}"
        if tag.lower() in seen:
            continue
        seen.add(tag.lower())
        result.append(tag)
    return result[:MAX_HASHTAGS]


def hashtag_agent(client: OpenAI, brief: str, draft: str) -> List[str]:
    """Agent 3: generate relevant hashtags."""
    system = (
        "You are the Hashtag Agent for a fintech LinkedIn content team. "
        'Reply with JSON only: {"hashtags": ["#Example"]}.'
    )
    user = f"{brief}\n\nPost:\n{draft}\n\nReturn 3 to {MAX_HASHTAGS} relevant, non-spammy hashtags."
    reply = run_agent(client, "hashtags", system, user, temperature=0.4)
    return normalise_hashtags(reply.get("hashtags"))


def clamp_confidence(value: Any) -> float:
    """Coerce score into the range 0 to 1."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(1.0, number)), 2)


def critic_agent(client: OpenAI, brief: str, draft: str) -> Tuple[float, str]:
    """Agent 4: score draft quality and brand fit."""
    system = (
        "You are the Critic Agent for a fintech LinkedIn content team. Be strict. "
        'Reply with JSON only: {"confidence": 0.0, "reason": "..."}.'
    )
    user = (
        f"{brief}\n\nPost:\n{draft}\n\n"
        "Score from 0 to 1 on brand voice fit, regulatory safety, clarity, and engagement. "
        "Give one short reason."
    )
    reply = run_agent(client, "critic", system, user, temperature=0.2)
    return clamp_confidence(reply.get("confidence")), str(reply.get("reason", "")).strip()


def find_forbidden(draft: str, forbidden_topics: List[str]) -> List[str]:
    """Return forbidden topics found in draft."""
    lowered = draft.lower()
    return [topic for topic in forbidden_topics if topic.strip() and topic.lower() in lowered]


def mock_pipeline(req: LinkedInRequest) -> Tuple[List[str], str, List[str], float, str]:
    """Deterministic AutoGen-style output for Ubuntu smoke tests without OpenAI."""
    hints = req.context.topic_hints or ["digital payments"]
    ideas = [
        f"How {req.brand.name} helps SMEs adopt {hint}"
        for hint in hints[: req.options.idea_count]
    ]
    while len(ideas) < req.options.idea_count:
        ideas.append(f"Practical compliance habits for {req.context.audience}")
    draft = (
        f"India's payments landscape is moving in real time.\n\n"
        f"At {req.brand.name}, we see teams win when security and speed ship together. "
        f"Focus this week: {hints[0]}. Build controls that customers can trust without "
        f"slowing settlement.\n\n"
        f"{req.brand.cta_style}."
    )
    hashtags = ["#Fintech", "#DigitalPayments", "#IndiaFinance", "#SME"][:MAX_HASHTAGS]
    confidence = 0.82
    reason = "Mock critic: on-brand, clear hook, no forbidden topics."
    return ideas, draft, hashtags, confidence, reason


@app.get("/health")
def health() -> Dict[str, Any]:
    """Report service liveness and config state."""
    return {
        "status": "ok",
        "model": MODEL,
        "openai_key_configured": bool(os.getenv("OPENAI_API_KEY")),
        "mock_llm": MOCK_LLM,
    }


@app.post("/linkedin", response_model=LinkedInResponse)
def create_linkedin_post(req: LinkedInRequest) -> LinkedInResponse:
    """Run four agents and return structured post data."""
    started = datetime.now(timezone.utc)
    timer = time.perf_counter()

    if MOCK_LLM:
        ideas, draft, hashtags, confidence, reason = mock_pipeline(req)
    else:
        client = get_client()
        brief = brand_brief(req)
        ideas = ideation_agent(client, brief, req.options.idea_count)
        draft = drafting_agent(client, brief, ideas)
        hashtags = hashtag_agent(client, brief, draft)
        confidence, reason = critic_agent(client, brief, draft)

    flags = find_forbidden(draft, req.brand.forbidden_topics)
    if flags:
        confidence = min(confidence, FORBIDDEN_CONFIDENCE_CAP)
        logger.warning("Forbidden topics in draft: %s", flags)

    finished = datetime.now(timezone.utc)
    duration_ms = int((time.perf_counter() - timer) * 1000)
    logger.info("Run complete: confidence=%.2f duration_ms=%d mock=%s", confidence, duration_ms, MOCK_LLM)

    return LinkedInResponse(
        ideas=ideas,
        draft=draft,
        confidence=confidence,
        hashtags=hashtags,
        meta=Meta(
            model=("mock" if MOCK_LLM else MODEL),
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            duration_ms=duration_ms,
            agents=AGENT_NAMES,
            critic_reason=reason,
            flags=flags,
            meets_threshold=confidence >= req.options.min_confidence,
        ),
    )
