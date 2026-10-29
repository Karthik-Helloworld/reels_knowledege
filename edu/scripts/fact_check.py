"""Second pass: check each claim against the source it cites.

Ollama uses the same host and model as script generation. Rule checks then
require every number, date, and name in the claim to appear in that source
text. A failed claim blocks the video.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict

from core.utils import ensure_parent
from edu.niche import NicheConfig, load_niche
from edu.scripts.script_gen import GroundingError, ReelScript, ollama_chat
from edu.sources.http import make_client
from edu.sources.model import SourceDocument

FACT_SYSTEM = """You verify educational reel claims against the cited source sentences.
Use only those sentences. Do not use outside knowledge.
supported is true only when one of the cited source sentences states the claim.
A paraphrase is supported only if a sentence contains the same fact.
If the claim adds a number, date, name, or cause the sentences do not state, supported is false.
Return one JSON object and nothing else:
{"claims":[{"id":"...","supported":true,"reason":"short reason"}]}
Include every claim id from the user message.
"""

_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|"
    "November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
_LONG_DATE = re.compile(
    rf"\b(?:{_MONTHS})\.?\s+\d{{1,2}}(?:,\s*|\s+)\d{{4}}\b"
    rf"|\b\d{{1,2}}\s+(?:{_MONTHS})\.?\s+\d{{4}}\b"
    rf"|\b(?:{_MONTHS})\.?\s+\d{{4}}\b"
    rf"|\b\d{{4}}-\d{{2}}-\d{{2}}\b",
    re.IGNORECASE,
)
_YEAR = re.compile(r"\b(?:1[0-9]{3}|20[0-9]{2})\b")
_NUMBER = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+(?:\.\d+)?\b")
_MULTI_NAME = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")
_SINGLE_NAME = re.compile(r"\b[A-Z][a-z]{2,}\b|\b[A-Z]{2,}\b")
_MONTH_WORDS = {
    word.casefold()
    for word in (
        "January February March April May June July August September October "
        "November December Jan Feb Mar Apr Jun Jul Aug Sep Sept Oct Nov Dec"
    ).split()
}
_STOPWORDS = {
    "a", "an", "the", "this", "that", "these", "those", "it", "its", "in", "on",
    "at", "to", "for", "of", "and", "or", "but", "if", "when", "where", "why",
    "how", "what", "who", "we", "you", "they", "he", "she", "i", "our", "their",
    "from", "with", "as", "by", "not", "no", "yes", "there", "here", "then",
    "so", "because", "about", "into", "over", "after", "before", "during",
    "between", "through", "above", "below", "up", "down", "out", "all", "some",
    "any", "each", "every", "both", "few", "more", "most", "other", "such",
    "only", "also", "just", "than", "too", "very", "can", "could", "should",
    "would", "may", "might", "must", "will", "shall", "do", "does", "did", "is",
    "are", "was", "were", "be", "been", "being", "have", "has", "had", "source",
}


class FactCheckError(RuntimeError):
    """The report failed, so the video must not continue."""


class ClaimReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    claim: str
    source_urls: list[str]
    source_text: str
    source_sentences: list[str]
    llm_supported: bool
    llm_reason: str
    rule_failures: list[str]
    passed: bool


class FactReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    blocked: bool
    model: str
    fixture: bool
    claims: list[ClaimReport]


class _Claim:
    def __init__(
        self,
        claim_id: str,
        text: str,
        rule_text: str,
        source_urls: list[str],
        source_text: str,
    ) -> None:
        self.id = claim_id
        self.text = text
        self.rule_text = rule_text
        self.source_urls = source_urls
        self.source_text = source_text
        self.source_sentences = split_sentences(source_text)


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    cleaned = [part.strip() for part in parts if part.strip()]
    return cleaned or ([text.strip()] if text.strip() else [])


def build_claims(script: ReelScript, documents: list[SourceDocument]) -> list[_Claim]:
    by_url = {document.url: document for document in documents}
    cited: list[SourceDocument] = []
    claims: list[_Claim] = []
    missing: list[_Claim] = []
    for index, beat in enumerate(script.beats):
        document = by_url.get(beat.source_url)
        rule_text = " ".join(part for part in (beat.narration, beat.on_screen) if part)
        if document is None:
            missing.append(
                _Claim(
                    f"beat-{index}",
                    beat.narration,
                    rule_text,
                    [beat.source_url],
                    "",
                )
            )
            continue
        cited.append(document)
        claims.append(
            _Claim(
                f"beat-{index}",
                beat.narration,
                rule_text,
                [document.url],
                document.text,
            )
        )
    shared_urls = list(dict.fromkeys(document.url for document in cited))
    shared_text = "\n".join(dict.fromkeys(document.text for document in cited))
    shared = _Claim("hook", script.hook, script.hook, shared_urls, shared_text)
    closing = _Claim("closing", script.closing, script.closing, shared_urls, shared_text)
    return [shared, *claims, *missing, closing]


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < other_end and other_start < end for other_start, other_end in spans)


def _number_in_text(number: str, source: str) -> bool:
    normalized = number.replace(",", "")
    haystack = source.replace(",", "")
    return re.search(rf"(?<!\d){re.escape(normalized)}(?!\d)", haystack) is not None


def _date_in_text(phrase: str, source: str) -> bool:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", phrase):
        return phrase in source
    if not any(re.search(rf"\b{month}\b", phrase, re.IGNORECASE) for month in _MONTH_WORDS):
        return _number_in_text(phrase, source)
    months = [
        month
        for month in _MONTH_WORDS
        if re.search(rf"\b{month}\b", phrase, re.IGNORECASE)
    ]
    if any(
        re.search(rf"\b{re.escape(month)}\b", source, re.IGNORECASE) is None
        for month in months
    ):
        return False
    return all(_number_in_text(number, source) for number in _NUMBER.findall(phrase))


def _name_in_text(name: str, source: str) -> bool:
    return re.search(rf"\b{re.escape(name)}\b", source, re.IGNORECASE) is not None


def extract_names(text: str) -> list[str]:
    names: list[str] = []
    spans: list[tuple[int, int]] = []
    for match in _MULTI_NAME.finditer(text):
        parts = match.group(0).split()
        while parts and parts[0].casefold() in _STOPWORDS:
            parts = parts[1:]
        while parts and parts[-1].casefold() in _STOPWORDS:
            parts = parts[:-1]
        if not parts:
            continue
        if len(parts) == 1:
            token = parts[0]
            if token.casefold() in _STOPWORDS or token.casefold() in _MONTH_WORDS:
                continue
            names.append(token)
        else:
            names.append(" ".join(parts))
        spans.append(match.span())
    for match in _SINGLE_NAME.finditer(text):
        if _overlaps(match.span(), spans):
            continue
        token = match.group(0)
        if token.casefold() in _STOPWORDS or token.casefold() in _MONTH_WORDS:
            continue
        names.append(token)
        spans.append(match.span())
    unique: list[str] = []
    for name in names:
        if name not in unique:
            unique.append(name)
    return unique


def rule_failures(claim: str, source_text: str) -> list[str]:
    if not source_text.strip():
        return ["cited source was not retrieved"]
    failures: list[str] = []
    date_spans = [match.span() for match in _LONG_DATE.finditer(claim)]
    date_spans.extend(
        match.span()
        for match in _YEAR.finditer(claim)
        if not _overlaps(match.span(), date_spans)
    )
    for match in _NUMBER.finditer(claim):
        if _overlaps(match.span(), date_spans):
            continue
        token = match.group(0)
        if not _number_in_text(token, source_text):
            failures.append(f"number not in source: {token}")
    for match in _LONG_DATE.finditer(claim):
        phrase = match.group(0)
        if not _date_in_text(phrase, source_text):
            failures.append(f"date not in source: {phrase}")
    for match in _YEAR.finditer(claim):
        if _overlaps(match.span(), [item.span() for item in _LONG_DATE.finditer(claim)]):
            continue
        phrase = match.group(0)
        if not _date_in_text(phrase, source_text):
            failures.append(f"date not in source: {phrase}")
    for name in extract_names(claim):
        if not _name_in_text(name, source_text):
            failures.append(f"name not in source: {name}")
    return failures


def build_messages(claims: list[_Claim]) -> list[dict[str, str]]:
    payload = {
        "claims": [
            {
                "id": claim.id,
                "claim": claim.text,
                "source_urls": claim.source_urls,
                "source_sentences": claim.source_sentences,
            }
            for claim in claims
            if claim.source_text
        ]
    }
    user = (
        "Verify every claim against its cited source sentences.\n"
        + json.dumps(payload, indent=2)
    )
    return [
        {"role": "system", "content": FACT_SYSTEM},
        {"role": "user", "content": user},
    ]


def parse_verdicts(payload: dict, claim_ids: list[str]) -> dict[str, tuple[bool, str]]:
    raw_claims = payload.get("claims") if isinstance(payload, dict) else None
    if not isinstance(raw_claims, list):
        raise FactCheckError(
            "fact check blocked the video. Ollama verdict was unusable: missing claims"
        )
    found: dict[str, tuple[bool, str]] = {}
    for item in raw_claims:
        if not isinstance(item, dict):
            continue
        claim_id = str(item.get("id") or "").strip()
        if not claim_id:
            continue
        supported = item.get("supported")
        reason = str(item.get("reason") or "").strip()
        if not isinstance(supported, bool):
            found[claim_id] = (False, reason or "verdict was not a boolean")
        else:
            found[claim_id] = (
                supported,
                reason or ("supported" if supported else "not supported"),
            )
    return {
        claim_id: found.get(claim_id, (False, "model omitted this claim"))
        for claim_id in claim_ids
    }


def load_llm_fixture(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise FactCheckError(f"{path} must be a JSON object")
    if "claims" in raw:
        return raw
    message = raw.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, dict):
            return content
        if isinstance(content, str) and content.strip():
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed
    raise FactCheckError(f"{path} has no claims verdict")


def fact_check(
    script: ReelScript,
    documents: list[SourceDocument],
    *,
    niche: NicheConfig,
    client: httpx.Client | None = None,
    llm_payload: dict | None = None,
) -> FactReport:
    claims = build_claims(script, documents)
    checkable = [claim for claim in claims if claim.source_text]
    payload = llm_payload
    if payload is None:
        if client is None:
            raise FactCheckError("fact check blocked the video. No Ollama client was provided.")
        if not checkable:
            payload = {"claims": []}
        else:
            try:
                payload = ollama_chat(
                    client,
                    niche.ollama_host,
                    niche.ollama_model,
                    build_messages(checkable),
                )
            except GroundingError as exc:
                raise FactCheckError(
                    f"fact check blocked the video. Ollama verdict was unusable: {exc}"
                ) from exc
    verdicts = parse_verdicts(payload, [claim.id for claim in checkable])
    results: list[ClaimReport] = []
    for claim in claims:
        if not claim.source_text:
            supported = False
            reason = "cited source was not retrieved"
            failures = ["cited source was not retrieved"]
        else:
            supported, reason = verdicts[claim.id]
            failures = rule_failures(claim.rule_text, claim.source_text)
        passed = supported and not failures
        results.append(
            ClaimReport(
                id=claim.id,
                claim=claim.text,
                source_urls=claim.source_urls,
                source_text=claim.source_text,
                source_sentences=claim.source_sentences,
                llm_supported=supported,
                llm_reason=reason,
                rule_failures=failures,
                passed=passed,
            )
        )
    passed = bool(results) and all(item.passed for item in results)
    return FactReport(
        passed=passed,
        blocked=not passed,
        model=niche.ollama_model,
        fixture=llm_payload is not None,
        claims=results,
    )


def format_block_message(report: FactReport) -> str:
    failed = [claim for claim in report.claims if not claim.passed]
    noun = "claim" if len(failed) == 1 else "claims"
    lines = [f"fact check blocked the video. {len(failed)} {noun} failed."]
    for claim in failed:
        details: list[str] = []
        if not claim.llm_supported:
            reason = claim.llm_reason.strip()
            if reason.casefold().startswith("low support"):
                details.append(reason)
            else:
                details.append(f"low support: {reason}")
        details.extend(claim.rule_failures)
        lines.append(f"- {claim.id}: {'; '.join(details)}")
    return "\n".join(lines)


def write_report(report: FactReport, path: Path) -> Path:
    ensure_parent(path)
    path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def package_parts(package: dict) -> tuple[ReelScript, list[SourceDocument]]:
    script = ReelScript.model_validate(package["script"])
    documents = [SourceDocument.model_validate(item) for item in package["sources"]]
    return script, documents


def default_report_path(script_path: Path) -> Path:
    return script_path.parent / "fact_report.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fact-check a saved reel package. A fixture skips the live model."
    )
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--llm-fixture", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--niche", type=Path, default=None)
    args = parser.parse_args(argv)
    package = json.loads(args.package.read_text(encoding="utf-8"))
    script, documents = package_parts(package)
    niche = load_niche(args.niche)
    llm_payload = load_llm_fixture(args.llm_fixture) if args.llm_fixture else None
    report_path = args.report or default_report_path(args.package)
    try:
        if llm_payload is None:
            with make_client() as client:
                report = fact_check(
                    script,
                    documents,
                    niche=niche,
                    client=client,
                    llm_payload=None,
                )
        else:
            report = fact_check(
                script,
                documents,
                niche=niche,
                llm_payload=llm_payload,
            )
    except (FactCheckError, GroundingError, httpx.HTTPError, ValueError, OSError) as exc:
        print(f"fact-check: {exc}", file=sys.stderr)
        return 1
    write_report(report, report_path)
    if not report.passed:
        print(format_block_message(report), file=sys.stderr)
        print(f"Report: {report_path}", file=sys.stderr)
        return 1
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
