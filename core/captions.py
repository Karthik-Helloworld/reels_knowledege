"""Word-level captions via faster-whisper.

The package is optional. Importing this module never loads a model. Calling
``transcribe`` without faster-whisper raises ``CaptionsUnavailable``.
Hello-world video render does not call this module.

On a MacBook Air M4 (16GB), install with the project venv and keep the
default CPU int8 ``base`` model:

    .venv/bin/pip install -r core/requirements-speech.txt
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path

INSTALL_HINT = (
    "Install faster-whisper for word-level captions: "
    ".venv/bin/pip install -r core/requirements-speech.txt. "
    "Default model is WHISPER_MODEL=base on CPU int8, which fits a 16GB machine. "
    "The hello-world Remotion render does not need captions. "
    "See the Phase 0 section of README.md."
)


class CaptionsUnavailable(RuntimeError):
    """Raised when faster-whisper is not installed."""


@dataclass(frozen=True)
class WordCaption:
    text: str
    start: float
    end: float


def whisper_available() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def format_srt_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def words_to_srt(words: list[WordCaption]) -> str:
    """One SRT cue per word. Empty text is skipped."""

    blocks: list[str] = []
    index = 1
    for word in words:
        text = word.text.strip()
        if not text:
            continue
        blocks.append(
            f"{index}\n"
            f"{format_srt_timestamp(word.start)} --> {format_srt_timestamp(word.end)}\n"
            f"{text}\n"
        )
        index += 1
    return "\n".join(blocks)


def write_srt(words: list[WordCaption], path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(words_to_srt(words), encoding="utf-8")
    return destination


def transcribe(
    audio_path: str | Path,
    *,
    model_size: str | None = None,
    device: str | None = None,
    compute_type: str | None = None,
) -> list[WordCaption]:
    """Return word-level timestamps for ``audio_path``.

    Loads faster-whisper only when called. ``model_size`` defaults to the
    ``WHISPER_MODEL`` env var or ``base``.
    """

    path = Path(audio_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    if not whisper_available():
        raise CaptionsUnavailable(INSTALL_HINT)

    from faster_whisper import WhisperModel

    model = WhisperModel(
        model_size or os.environ.get("WHISPER_MODEL", "base"),
        device=device or os.environ.get("WHISPER_DEVICE", "cpu"),
        compute_type=compute_type or os.environ.get("WHISPER_COMPUTE_TYPE", "int8"),
    )
    segments, _info = model.transcribe(str(path), word_timestamps=True)
    words: list[WordCaption] = []
    for segment in segments:
        segment_words = getattr(segment, "words", None) or []
        if segment_words:
            for token in segment_words:
                words.append(
                    WordCaption(
                        text=str(token.word),
                        start=float(token.start),
                        end=float(token.end),
                    )
                )
            continue
        text = str(getattr(segment, "text", "") or "").strip()
        if text:
            words.append(
                WordCaption(
                    text=text,
                    start=float(segment.start),
                    end=float(segment.end),
                )
            )
    return words
