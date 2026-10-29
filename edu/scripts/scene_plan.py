"""Turn a fact-checked package into scene timing, credits, and a post caption.

Visual scenes are drawn by Remotion. This module only decides what they show
and how long they last. The spoken timeline follows the audio file. A missing
TTS engine uses a 36 second tone so the file lands in the 30-45 second target.
The source card is always two seconds after that audio.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from core.utils import VIDEO_FPS
from edu.sources.nasa import public_domain_or_cc0
from edu.sources.wikimedia import license_reusable

CREDIT_SECONDS = 2.0
TARGET_MIN_SECONDS = 30.0
TARGET_MAX_SECONDS = 45.0
TARGET_SPOKEN_SECONDS = 36.0
SAFE_BOTTOM = 250
SAFE_RIGHT = 120

_SCENE_WEIGHTS = (
    ("hook", 0.14),
    ("concept", 0.16),
    ("diagram", 0.18),
    ("timeline", 0.18),
    ("stat", 0.16),
    ("cta", 0.18),
)
_HASHTAG_FILL = (
    "ScienceFacts",
    "Space",
    "Astronomy",
    "Physics",
    "LearnScience",
    "DidYouKnow",
    "SpaceFacts",
    "Educational",
)


def narration_text(script: dict) -> str:
    parts = [str(script.get("hook") or "")]
    for beat in script.get("beats") or []:
        if isinstance(beat, dict):
            parts.append(str(beat.get("narration") or ""))
    parts.append(str(script.get("closing") or ""))
    parts.append(str(script.get("cta") or ""))
    return " ".join(part.strip() for part in parts if part.strip())


def spoken_seconds(measured: float | None, backend: str) -> float:
    """Length of the audio that plays under the scenes.

    A generated tone is 36 seconds. Real speech shorter than 30 seconds is
    padded with silence to that same length. Longer speech is kept as recorded,
    including takes that run past 45 seconds.
    """

    if backend == "tone" or measured is None or measured <= 0:
        return TARGET_SPOKEN_SECONDS
    if measured < TARGET_MIN_SECONDS:
        return TARGET_SPOKEN_SECONDS
    return measured


def scene_spans(spoken: float, fps: int = VIDEO_FPS) -> list[dict]:
    spoken_frames = max(len(_SCENE_WEIGHTS), int(round(spoken * fps)))
    names = [name for name, _weight in _SCENE_WEIGHTS]
    weights = [weight for _name, weight in _SCENE_WEIGHTS]
    frames = [max(1, int(spoken_frames * weight)) for weight in weights]
    frames[-1] = spoken_frames - sum(frames[:-1])
    if frames[-1] < 1:
        frames[-1] = 1
        overflow = sum(frames) - spoken_frames
        for index in range(len(frames) - 2, -1, -1):
            spare = frames[index] - 1
            take = min(spare, overflow)
            frames[index] -= take
            overflow -= take
            if overflow <= 0:
                break
    spans: list[dict] = []
    cursor = 0
    for name, count in zip(names, frames, strict=True):
        spans.append({"id": name, "from": cursor, "duration": count})
        cursor += count
    credit_frames = max(1, int(round(CREDIT_SECONDS * fps)))
    spans.append({"id": "sourceCredit", "from": cursor, "duration": credit_frames})
    return spans


def even_captions(text: str, spoken: float) -> list[dict]:
    words = [word for word in text.split() if word.strip()]
    if not words or spoken <= 0:
        return []
    step = spoken / len(words)
    return [
        {"text": word, "start": round(index * step, 4), "end": round((index + 1) * step, 4)}
        for index, word in enumerate(words)
    ]


def hashtags(topic: str) -> list[str]:
    tags: list[str] = []
    for raw in re.split(r"[^A-Za-z0-9]+", topic):
        if len(raw) < 3:
            continue
        tag = raw[:1].upper() + raw[1:]
        if tag not in tags:
            tags.append(tag)
    for extra in _HASHTAG_FILL:
        if len(tags) >= 8:
            break
        if extra not in tags:
            tags.append(extra)
    return [f"#{tag}" for tag in tags[:8]]


def credit_line(author: str, license_name: str) -> str:
    return f"Source: {author}, {license_name}"


def ken_burns_source(document: dict) -> dict | None:
    """Wikimedia and NASA stills only, and only when the license allows reuse."""

    image_url = str(document.get("image_url") or "").strip()
    if not image_url.startswith(("http://", "https://")):
        return None
    license_name = str(document.get("license") or "").strip()
    page_host = urlparse(str(document.get("url") or "")).netloc.casefold()
    image_host = urlparse(image_url).netloc.casefold()
    nasa = page_host.endswith("nasa.gov") or image_host.endswith("nasa.gov")
    commons = "wikimedia.org" in page_host or "wikimedia.org" in image_host
    if nasa and public_domain_or_cc0(license_name):
        allowed = True
    elif commons and license_reusable(license_name):
        allowed = True
    else:
        allowed = False
    if not allowed:
        return None
    return {
        "url": image_url,
        "license": license_name,
        "title": str(document.get("title") or "").strip(),
    }


def post_caption(package: dict) -> str:
    topic = str(package.get("topic") or "").strip()
    lines = [topic, ""]
    for source in package.get("sources") or []:
        if not isinstance(source, dict):
            continue
        lines.append(str(source.get("title") or "").strip())
        lines.append(str(source.get("url") or "").strip())
        lines.append(str(source.get("license") or "").strip())
        lines.append(str(source.get("author") or "").strip())
        lines.append("")
    tags = hashtags(topic)
    lines.append(" ".join(tags))
    return "\n".join(lines).rstrip() + "\n"


def _stat(script: dict) -> tuple[str, str]:
    blob = narration_text(script)
    match = re.search(r"\d[\d,]*(?:\.\d+)?", blob)
    if match:
        return match.group(0), "stated in the cited source"
    takeaway = str(script.get("key_takeaway") or script.get("hook") or "").strip()
    words = takeaway.split()
    if len(words) > 4:
        return " ".join(words[:2]), " ".join(words[2:])
    return takeaway or "Fact", ""


def build_props(
    package: dict,
    *,
    spoken: float,
    audio_file: str | None,
    image_file: str | None,
    captions: list[dict],
) -> dict:
    script = package["script"]
    beats = [beat for beat in script.get("beats") or [] if isinstance(beat, dict)]
    nodes = [str(beat.get("on_screen") or "").strip() for beat in beats]
    nodes = [node for node in nodes if node]
    timeline = []
    for index, beat in enumerate(beats[:4]):
        detail = str(beat.get("on_screen") or beat.get("narration") or "")
        timeline.append({"label": str(index + 1), "detail": detail})
    stat_value, stat_label = _stat(script)
    credits = []
    for source in package.get("sources") or []:
        if not isinstance(source, dict):
            continue
        author = str(source.get("author") or "").strip()
        license_name = str(source.get("license") or "").strip()
        if author and license_name:
            credits.append(
                {
                    "name": author,
                    "license": license_name,
                    "line": credit_line(author, license_name),
                }
            )
    if not credits:
        credits.append(
            {
                "name": "Source",
                "license": "see caption",
                "line": "Source: Source, see caption",
            }
        )
    return {
        "topic": str(package.get("topic") or script.get("topic") or ""),
        "hook": str(script.get("hook") or ""),
        "concept": str(script.get("key_takeaway") or script.get("hook") or ""),
        "diagramTitle": nodes[0] if nodes else str(script.get("hook") or ""),
        "diagramNodes": nodes or [str(script.get("hook") or "Fact")],
        "timeline": timeline or [{"label": "1", "detail": str(script.get("closing") or "")}],
        "statValue": stat_value,
        "statLabel": stat_label,
        "cta": str(script.get("cta") or ""),
        "credits": credits,
        "captions": captions,
        "audioFile": audio_file,
        "imageFile": image_file,
        "spokenSeconds": spoken,
        "creditSeconds": CREDIT_SECONDS,
        "scenes": scene_spans(spoken),
    }
