"""Ask local Ollama for a reel script grounded in the fetched sources.

The model must return JSON. Every beat has to cite a URL that was actually
retrieved. This is not a fact checker: it only rejects scripts that point
outside the source set or that do not match the JSON shape.
"""

from __future__ import annotations

import json
import re

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from edu.niche import NicheConfig
from edu.sources.model import SourceDocument

SYSTEM_PROMPT = """You write a short educational reel script as JSON.
Use only the source text the user provides. Do not add facts from anywhere else.
The angle is only a hint. It is not a source. If the sources do not support it, ignore it.
Every beat's source_url must be copied exactly from one of the sources.
If a source does not support a sentence, leave that sentence out.
Return one JSON object and nothing else, with this shape:
{"topic":"...","hook":"...","beats":[{"narration":"...","on_screen":"...","source_url":"..."}],"closing":"..."}
Write 3 beats when the sources support them. hook, narration, on_screen, and closing must be non-empty.
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


class ReelScript(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str
    hook: str
    beats: list[ScriptBeat]
    closing: str

    @field_validator("topic", "hook", "closing")
    @classmethod
    def not_blank(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned

    @field_validator("beats")
    @classmethod
    def beat_count(cls, beats: list[ScriptBeat]) -> list[ScriptBeat]:
        if not 1 <= len(beats) <= 6:
            raise ValueError("need 1 to 6 beats")
        return beats


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


def assert_grounded(script: ReelScript, documents: list[SourceDocument]) -> None:
    allowed = {document.url for document in documents}
    if not allowed:
        raise GroundingError("no sources to ground the script")
    for beat in script.beats:
        if beat.source_url not in allowed:
            raise GroundingError(
                f"beat cites a url that was not in the sources: {beat.source_url}"
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
) -> dict:
    response = client.post(
        host.rstrip("/") + "/api/chat",
        json={
            "model": model,
            "stream": False,
            "format": "json",
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
    payload = ollama_chat(client, niche.ollama_host, niche.ollama_model, messages)
    script = script_from_payload(payload)
    if script.topic.casefold() != topic.strip().casefold():
        raise GroundingError("script topic does not match the requested topic")
    assert_grounded(script, documents)
    return script
