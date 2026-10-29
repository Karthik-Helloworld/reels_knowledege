"""Ask local Ollama for a reel script grounded in the fetched sources.

The model must return JSON. Each claim cites a source URL and a sentence
copied from that source. Malformed JSON is retried before the run fails.
This is not a fact checker: it checks citations, not whether the claim is true.
"""

from __future__ import annotations

import json
import re

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from edu.niche import NicheConfig
from edu.sources.model import SourceDocument

HOOK_MAX_CHARS = 140
OLLAMA_ATTEMPTS = 3

SYSTEM_PROMPT = """You write a short educational reel script as JSON.
Use only the source text the user provides. Do not add facts from anywhere else.
The angle is only a hint. It is not a source. If the sources do not support it, ignore it.
Every source_url must be copied exactly from one of the sources.
Every claim's source_sentence must be copied verbatim from that source's text.
Return one JSON object and nothing else, with this shape:
{"topic":"...","hook":"...","beats":[{"narration":"...","on_screen":"...","source_url":"..."}],
"closing":"...","key_takeaway":"...","cta":"...",
"claims":[{"text":"...","source_url":"...","source_sentence":"..."}]}
The hook must be at most 140 characters.
key_takeaway is one sentence. cta is a short call to action.
Write 3 beats when the sources support them. Include at least one claim.
"""


class GroundingError(ValueError):
    """The script is missing, off-topic, or cites a URL we did not retrieve."""


class ScriptBeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    narration: str
    on_screen: str
    source_url: str

    @field_validator("narration", "on_screen")
    @classmethod
    def not_blank(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned

    @field_validator("source_url")
    @classmethod
    def http_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("source_url must be an http(s) url")
        return cleaned


class ScriptClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    source_url: str
    source_sentence: str

    @field_validator("text", "source_sentence")
    @classmethod
    def not_blank(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned

    @field_validator("source_url")
    @classmethod
    def http_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("source_url must be an http(s) url")
        return cleaned


class ReelScript(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str
    hook: str
    beats: list[ScriptBeat]
    closing: str
    key_takeaway: str
    cta: str
    claims: list[ScriptClaim]

    @field_validator("topic", "closing", "key_takeaway", "cta")
    @classmethod
    def not_blank(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned

    @field_validator("hook")
    @classmethod
    def hook_cap(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("must not be blank")
        if len(cleaned) > HOOK_MAX_CHARS:
            raise ValueError(f"hook must be at most {HOOK_MAX_CHARS} characters")
        return cleaned

    @field_validator("beats")
    @classmethod
    def beat_count(cls, beats: list[ScriptBeat]) -> list[ScriptBeat]:
        if not 1 <= len(beats) <= 6:
            raise ValueError("need 1 to 6 beats")
        return beats

    @field_validator("claims")
    @classmethod
    def claim_count(cls, claims: list[ScriptClaim]) -> list[ScriptClaim]:
        if not claims:
            raise ValueError("need at least one claim")
        return claims


def build_messages(
    topic: str,
    angle: str,
    niche: NicheConfig,
    documents: list[SourceDocument],
) -> list[dict[str, str]]:
    payload = {
        "niche": niche.name,
        "audience": niche.audience,
        "tone": niche.tone,
        "length_seconds": niche.length_seconds,
        "topic": topic,
        "angle": angle,
        "sources": [
            {
                "title": document.title,
                "text": document.text,
                "url": document.url,
                "license": document.license,
                "author": document.author,
            }
            for document in documents
        ],
    }
    user = (
        "Write the reel script for this topic. "
        "Ground every beat in the sources below.\n"
        + json.dumps(payload, indent=2)
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def normalize_ws(value: str) -> str:
    return " ".join(value.split())


def assert_grounded(script: ReelScript, documents: list[SourceDocument]) -> None:
    by_url = {document.url: document for document in documents}
    if not by_url:
        raise GroundingError("no sources to ground the script")
    for beat in script.beats:
        if beat.source_url not in by_url:
            raise GroundingError(
                f"beat cites a url that was not in the sources: {beat.source_url}"
            )
    for claim in script.claims:
        document = by_url.get(claim.source_url)
        if document is None:
            raise GroundingError(
                f"claim cites a url that was not in the sources: {claim.source_url}"
            )
        sentence = normalize_ws(claim.source_sentence)
        if sentence not in normalize_ws(document.text):
            raise GroundingError(
                f"source_sentence is not in the cited source: {claim.source_sentence}"
            )


def script_from_payload(payload: dict) -> ReelScript:
    try:
        return ReelScript.model_validate(payload)
    except ValidationError as exc:
        raise GroundingError(f"script JSON did not match the schema: {exc}") from exc


def _strip_fence(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def ollama_chat(
    client: httpx.Client,
    host: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    schema: dict | None = None,
) -> dict:
    response = client.post(
        host.rstrip("/") + "/api/chat",
        json={
            "model": model,
            "stream": False,
            "format": schema if schema is not None else "json",
            "messages": messages,
        },
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise GroundingError(
            f"Ollama returned HTTP {exc.response.status_code}"
        ) from exc
    try:
        body = response.json()
    except json.JSONDecodeError as exc:
        raise GroundingError("Ollama returned no JSON object") from exc
    message = body.get("message") if isinstance(body, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, dict):
        return content
    if not isinstance(content, str) or not content.strip():
        raise GroundingError("Ollama returned no JSON object")
    try:
        parsed = json.loads(_strip_fence(content))
    except json.JSONDecodeError as exc:
        raise GroundingError("Ollama content was not JSON") from exc
    if not isinstance(parsed, dict):
        raise GroundingError("script JSON must be an object")
    return parsed


def generate_script(
    topic: str,
    angle: str,
    niche: NicheConfig,
    documents: list[SourceDocument],
    client: httpx.Client,
) -> ReelScript:
    if not documents:
        raise GroundingError("no sources to ground the script")
    messages = build_messages(topic, angle, niche, documents)
    last_error: GroundingError | None = None
    for _attempt in range(OLLAMA_ATTEMPTS):
        try:
            payload = ollama_chat(
                client,
                niche.ollama_host,
                niche.ollama_model,
                messages,
                schema=ReelScript.model_json_schema(),
            )
            script = script_from_payload(payload)
            if script.topic.casefold() != topic.strip().casefold():
                raise GroundingError("script topic does not match the requested topic")
            assert_grounded(script, documents)
            return script
        except GroundingError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error
