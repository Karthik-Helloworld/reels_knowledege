"""Local text-to-speech.

Primary backend: Piper (offline). Uses the ``piper`` Python package when it
is installed, otherwise the ``piper`` executable.

Optional fallback: edge-tts. That package calls Microsoft's public read-aloud
service, so it needs a network and is only used when Piper is not ready.

Neither backend is required to render the hello-world video. If both are
missing, ``synthesize`` raises ``TTSUnavailable`` with an install hint.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from core.utils import ensure_parent, which

INSTALL_HINT = (
    "Install Piper for offline TTS (primary), or edge-tts as an optional "
    "fallback. On a Mac: python3 -m venv .venv && "
    ".venv/bin/pip install piper-tts && "
    ".venv/bin/python -m piper.download_voices en_US-lessac-medium, then set "
    "PIPER_MODEL to the downloaded .onnx file. "
    "Optional fallback: .venv/bin/pip install edge-tts. "
    "The hello-world Remotion render does not need TTS. See the Phase 0 "
    "section of README.md."
)


class TTSUnavailable(RuntimeError):
    """Raised when no TTS backend can run."""


@dataclass(frozen=True)
class TTSResult:
    path: Path
    backend: str
    duration_sec: float | None


def piper_model_path() -> Path | None:
    raw = os.environ.get("PIPER_MODEL", "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


def piper_python_ready() -> bool:
    return importlib.util.find_spec("piper") is not None and piper_model_path() is not None


def piper_cli_ready() -> bool:
    binary = os.environ.get("PIPER_BIN", "piper")
    return which(binary) is not None and piper_model_path() is not None


def edge_tts_available() -> bool:
    return importlib.util.find_spec("edge_tts") is not None


def available_backends() -> list[str]:
    found: list[str] = []
    if piper_python_ready() or piper_cli_ready():
        found.append("piper")
    if edge_tts_available():
        found.append("edge-tts")
    return found


def select_backend(preferred: str | None = None) -> str:
    name = (preferred or "").strip() or None
    if name in {"edge_tts"}:
        name = "edge-tts"
    if name == "piper":
        if piper_python_ready() or piper_cli_ready():
            return "piper"
        raise TTSUnavailable(f"Piper is not available. {INSTALL_HINT}")
    if name == "edge-tts":
        if edge_tts_available():
            return "edge-tts"
        raise TTSUnavailable(f"edge-tts is not available. {INSTALL_HINT}")
    if name:
        raise TTSUnavailable(
            f"Unknown TTS backend {name!r}. Use 'piper' or 'edge-tts'."
        )
    if piper_python_ready() or piper_cli_ready():
        return "piper"
    if edge_tts_available():
        return "edge-tts"
    raise TTSUnavailable(INSTALL_HINT)


def build_piper_cli_command(
    piper_bin: str,
    model: str,
    out_path: str,
    speaker: str | None = None,
) -> list[str]:
    command = [piper_bin, "--model", model, "--output_file", out_path]
    if speaker:
        command.extend(["--speaker", speaker])
    return command


def wav_duration_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            if rate <= 0:
                return None
            return handle.getnframes() / float(rate)
    except (wave.Error, EOFError, OSError):
        return None


def synthesize(
    text: str,
    out_path: str | Path,
    *,
    voice: str | None = None,
    backend: str | None = None,
) -> TTSResult:
    """Write speech for ``text`` to ``out_path``.

    ``voice`` is a Piper speaker id or an edge-tts voice name, depending on
    the backend that runs. ``backend`` may be ``"piper"`` or ``"edge-tts"``.
    """

    cleaned = text.strip()
    if not cleaned:
        raise ValueError("text must not be empty")
    path = Path(out_path)
    ensure_parent(path)
    chosen = select_backend(backend)
    if chosen == "piper":
        _synthesize_piper(cleaned, path, voice)
    else:
        _synthesize_edge(cleaned, path, voice)
    if not path.is_file():
        raise TTSUnavailable(f"{chosen} did not write {path}")
    return TTSResult(path=path, backend=chosen, duration_sec=wav_duration_seconds(path))


def _speaker_id(voice: str | None) -> int | None:
    raw = voice if voice is not None else os.environ.get("PIPER_SPEAKER")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(str(raw).strip())
    except ValueError as exc:
        raise TTSUnavailable(
            f"Piper speaker must be an integer id, got {raw!r}."
        ) from exc


def _synthesize_piper(text: str, path: Path, voice: str | None) -> None:
    if piper_python_ready():
        _synthesize_piper_python(text, path, voice)
        return
    _synthesize_piper_cli(text, path, voice)


def _synthesize_piper_python(text: str, path: Path, voice: str | None) -> None:
    from piper import PiperVoice

    model = piper_model_path()
    if model is None:
        raise TTSUnavailable(f"PIPER_MODEL is not a file. {INSTALL_HINT}")
    loaded = PiperVoice.load(str(model))
    speaker = _speaker_id(voice)
    with wave.open(str(path), "wb") as wav_file:
        if hasattr(loaded, "synthesize_wav"):
            kwargs: dict[str, object] = {}
            if speaker is not None:
                from piper import SynthesisConfig

                kwargs["syn_config"] = SynthesisConfig(speaker_id=speaker)
            loaded.synthesize_wav(text, wav_file, **kwargs)
            return
        loaded.synthesize(text, wav_file, speaker_id=speaker)


def _synthesize_piper_cli(text: str, path: Path, voice: str | None) -> None:
    model = piper_model_path()
    binary_name = os.environ.get("PIPER_BIN", "piper")
    binary = which(binary_name)
    if binary is None or model is None:
        raise TTSUnavailable(f"Piper CLI is not available. {INSTALL_HINT}")
    speaker = voice if voice is not None else os.environ.get("PIPER_SPEAKER")
    if speaker is not None:
        speaker = str(speaker).strip() or None
    command = build_piper_cli_command(binary, str(model), str(path), speaker)
    completed = subprocess.run(
        command,
        input=text.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0 or not path.is_file():
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise TTSUnavailable(
            f"piper exited {completed.returncode}: {detail or 'no stderr'}"
        )


def _synthesize_edge(text: str, path: Path, voice: str | None) -> None:
    import edge_tts

    chosen = voice or os.environ.get("EDGE_TTS_VOICE", "en-US-AriaNeural")

    async def _save() -> None:
        communicate = edge_tts.Communicate(text, chosen)
        await communicate.save(str(path))

    asyncio.run(_save())
