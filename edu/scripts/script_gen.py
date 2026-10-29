"""Ask local Ollama for a reel script grounded in the fetched sources.

The model must return JSON. It does not write citation text. Each claim names
the source URL and the ids of the numbered sentences that support it. This
module copies those sentences in from the source. Hook, beats, closing, and
claim text are the model's own wording. Malformed JSON and unknown sentence
ids are retried before the run fails. Dates, numbers, and names are checked
later by the fact checker, not here.
"""

from __future__ import annotations

import json
import re

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from edu.niche import NicheConfig
from edu.sources.model import SourceDocument

HOOK_MAX_CHARS = 140
OLLAMA_ATTEMPTS = 3

SYSTEM_PROMPT = """You write a short educational reel script as JSON.
Use only the numbered source sentences the user provides. Do not add facts from anywhere else.
The angle is only a hint. It is not a source. If the sources do not support it, ignore it.
Every source_url must be copied exactly from one of the sources.
Do not write citation text. Each claim has text, source_url, and sentence_ids.
sentence_ids lists the ids of the numbered sentences that support that claim.
Use at least one id, and only ids from that source. Do not invent an id.
Write the hook, narration, on-screen line, closing, key takeaway, and claim text
in your own words. Do not paste the source sentence into every field.
Do not invent a number, a date, or a name that is not in the cited source sentence.
Every text field must be non-empty. An empty field is invalid.
on_screen is a short phrase, not a label such as Image or Diagram.
Return one JSON object and nothing else, with this shape:
{"topic":"...","hook":"...","beats":[{"narration":"...","on_screen":"...","source_url":"..."}],
"closing":"...","key_takeaway":"...","cta":"...",
"claims":[{"text":"...","source_url":"...","sentence_ids":["1"]}]}
The hook must be at most 140 characters.
key_takeaway is one sentence. cta is a short call to action and adds no facts.
Write 3 beats when the sources support them. Include at least one claim.
"""


class GroundingError(ValueError):
    """The script is missing, off-topic, or cites a URL we did not retrieve."""


class ScriptBeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    narration: str = Field(min_length=1)
    on_screen: str = Field(min_length=1)
    source_url: str = Field(min_length=1)

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

    text: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_sentence: str = Field(min_length=1)

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

    topic: str = Field(min_length=1)
    hook: str = Field(min_length=1)
    beats: list[ScriptBeat]
    closing: str = Field(min_length=1)
    key_takeaway: str = Field(min_length=1)
    cta: str = Field(min_length=1)
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


class DraftClaim(BaseModel):
    """What the model is allowed to write. Citation text is not one of the fields."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    sentence_ids: list[str] = Field(min_length=1)

    @field_validator("text")
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

    @field_validator("sentence_ids", mode="before")
    @classmethod
    def ids_are_strings(cls, value: object) -> list[str]:
        if not isinstance(value, list) or not value:
            raise ValueError("claim has no sentence id")
        cleaned: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                raise ValueError("claim has no sentence id")
            cleaned.append(text)
        return cleaned


class DraftScript(BaseModel):
    """Ollama's script. Claims cite sentence ids, not copied sentences."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1)
    hook: str = Field(min_length=1)
    beats: list[ScriptBeat]
    closing: str = Field(min_length=1)
    key_takeaway: str = Field(min_length=1)
    cta: str = Field(min_length=1)
    claims: list[DraftClaim]

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
    def claim_count(cls, claims: list[DraftClaim]) -> list[DraftClaim]:
        if not claims:
            raise ValueError("need at least one claim")
        return claims


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    cleaned = [part.strip() for part in parts if part.strip()]
    return cleaned or ([text.strip()] if text.strip() else [])


def numbered_sentences(text: str) -> dict[str, str]:
    return {str(index): sentence for index, sentence in enumerate(split_sentences(text), start=1)}


def build_messages(
    topic: str,
    angle: str,
    niche: NicheConfig,
    documents: list[SourceDocument],
    *,
    correction: str | None = None,
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
                "url": document.url,
                "license": document.license,
                "author": document.author,
                "sentences": [
                    {"id": sentence_id, "text": sentence}
                    for sentence_id, sentence in numbered_sentences(document.text).items()
                ],
            }
            for document in documents
        ],
    }
    user = (
        "Write the reel script for this topic. "
        "Cite each claim with sentence_ids from the numbered sentences. "
        "Use your own wording. Do not write the citation text.\n"
        + json.dumps(payload, indent=2)
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    if correction:
        messages.append(
            {
                "role": "user",
                "content": (
                    "The previous script was rejected. "
                    "Keep every claim's sentence_ids, and rewrite the hook, beats, "
                    "closing, and claims in your own words.\n"
                    + correction
                ),
            }
        )
    return messages


def normalize_ws(value: str) -> str:
    return " ".join(value.split())


def sentence_in_source(sentence: str, source: str) -> bool:
    """True when the citation is a case-insensitive substring of the source."""

    needle = normalize_ws(sentence).casefold()
    haystack = normalize_ws(source).casefold()
    return bool(needle) and needle in haystack


_WORD = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|[A-Za-z]+(?:'[A-Za-z]+)?")
_FUNCTION_WORDS = frozenset({"a", "an", "the"})


def _plain(value: str) -> str:
    return (
        value.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u2013", " ")
        .replace("\u2014", " ")
    )


def content_words(value: str) -> list[str]:
    return [match.group(0).casefold().replace(",", "") for match in _WORD.finditer(_plain(value))]


def unsupported_words(text: str, support: str) -> list[str]:
    """Words in ``text`` that do not appear in the cited source sentence.

    The articles a, an, and the are ignored. Every other word must be in the source.
    """

    allowed = set(content_words(support))
    extra: list[str] = []
    seen: set[str] = set()
    for match in _WORD.finditer(_plain(text)):
        word = match.group(0).replace(",", "")
        key = word.casefold()
        if key in _FUNCTION_WORDS or key in allowed or key in seen:
            continue
        seen.add(key)
        extra.append(word)
    return extra


def cited_sentence(claim: DraftClaim, documents: list[SourceDocument]) -> str:
    """Copy the exact source sentences named by the claim's ids."""

    by_url = {document.url: document for document in documents}
    document = by_url.get(claim.source_url)
    if document is None:
        raise GroundingError(
            f"claim cites a url that was not in the sources: {claim.source_url}"
        )
    if not claim.sentence_ids:
        raise GroundingError("claim has no sentence id")
    index = numbered_sentences(document.text)
    chosen: list[str] = []
    for sentence_id in claim.sentence_ids:
        sentence = index.get(sentence_id)
        if sentence is None:
            raise GroundingError(f"sentence id does not exist: {sentence_id}")
        chosen.append(sentence)
    return " ".join(chosen)


def resolve_citations(draft: DraftScript, documents: list[SourceDocument]) -> ReelScript:
    claims = [
        ScriptClaim(
            text=claim.text,
            source_url=claim.source_url,
            source_sentence=cited_sentence(claim, documents),
        )
        for claim in draft.claims
    ]
    return ReelScript(
        topic=draft.topic,
        hook=draft.hook,
        beats=draft.beats,
        closing=draft.closing,
        key_takeaway=draft.key_takeaway,
        cta=draft.cta,
        claims=claims,
    )


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
        if claim.source_url not in by_url:
            raise GroundingError(
                f"claim cites a url that was not in the sources: {claim.source_url}"
            )
        if not claim.source_sentence.strip():
            raise GroundingError("claim has no sentence id")


def script_from_payload(payload: dict, documents: list[SourceDocument]) -> ReelScript:
    try:
        draft = DraftScript.model_validate(payload)
    except ValidationError as exc:
        raise GroundingError(f"script JSON did not match the schema: {exc}") from exc
    return resolve_citations(draft, documents)


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
    *,
    correction: str | None = None,
) -> ReelScript:
    if not documents:
        raise GroundingError("no sources to ground the script")
    messages = build_messages(topic, angle, niche, documents, correction=correction)
    last_error: GroundingError | None = None
    for _attempt in range(OLLAMA_ATTEMPTS):
        try:
            payload = ollama_chat(
                client,
                niche.ollama_host,
                niche.ollama_model,
                messages,
                schema=DraftScript.model_json_schema(),
            )
            script = script_from_payload(payload, documents)
            if script.topic.casefold() != topic.strip().casefold():
                raise GroundingError("script topic does not match the requested topic")
            assert_grounded(script, documents)
            return script
        except GroundingError as exc:
            last_error = exc
            messages = [
                *build_messages(topic, angle, niche, documents, correction=correction),
                {
                    "role": "user",
                    "content": (
                        "Rejected: "
                        + str(exc)
                        + " Every claim needs at least one real sentence id from its "
                        "source. Do not write citation text. Rewrite the hook, beats, "
                        "closing, and claims in your own words, and do not invent a "
                        "number, a date, or a name."
                    ),
                },
            ]
    assert last_error is not None
    raise last_error
