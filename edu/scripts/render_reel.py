"""Render a fact-checked package with the core Remotion engine.

Piper and faster-whisper are optional. When they are missing, the render uses
a generated tone and word timings taken from the script. Wikimedia and NASA
images are downloaded only when their license allows Ken Burns motion.
"""

from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path

import httpx

from core.captions import CaptionsUnavailable, transcribe
from core.tts import TTSUnavailable, synthesize, wav_duration_seconds
from core.utils import ensure_parent, repo_root
from edu.scripts.scene_plan import (
    build_props,
    even_captions,
    ken_burns_source,
    narration_text,
    post_caption,
    spoken_seconds,
)

TONE_RATE = 22050
TONE_FREQ = 220.0


def write_tone(path: Path, seconds: float, rate: int = TONE_RATE) -> float:
    count = max(1, int(seconds * rate))
    ensure_parent(path)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = bytearray()
        for index in range(count):
            sample = int(0.08 * 32767 * math.sin(2 * math.pi * TONE_FREQ * index / rate))
            frames += struct.pack("<h", sample)
        handle.writeframes(frames)
    return wav_duration_seconds(path) or seconds


def extend_wav(path: Path, seconds: float) -> float:
    with wave.open(str(path), "rb") as handle:
        params = handle.getparams()
        frames = handle.readframes(handle.getnframes())
        rate = handle.getframerate() or 1
        current = handle.getnframes() / float(rate)
    if current >= seconds - 0.01:
        return current
    extra = int(round((seconds - current) * rate))
    silence = b"\x00" * extra * params.sampwidth * params.nchannels
    with wave.open(str(path), "wb") as handle:
        handle.setparams(params)
        handle.writeframes(frames + silence)
    return wav_duration_seconds(path) or seconds


def ensure_audio(text: str, path: Path) -> tuple[float, str]:
    try:
        result = synthesize(text, path)
    except TTSUnavailable:
        print(
            "edu-video: Piper and edge-tts are unavailable. "
            "Using a generated tone instead of speech. "
            "Install Piper for spoken audio. See the Phase 3 section of README.md.",
            file=sys.stderr,
        )
        duration = write_tone(path, spoken_seconds(None, "tone"))
        return duration, "tone"
    measured = result.duration_sec
    if measured is None:
        measured = wav_duration_seconds(path)
    target = spoken_seconds(measured, result.backend)
    if measured is not None and target > measured + 0.05:
        try:
            return extend_wav(path, target), result.backend
        except (wave.Error, EOFError, OSError):
            return measured, result.backend
    return measured if measured is not None else target, result.backend


def ensure_captions(audio: Path, text: str, spoken: float) -> tuple[list[dict], str]:
    try:
        words = transcribe(audio)
    except CaptionsUnavailable:
        print(
            "edu-video: faster-whisper is unavailable. "
            "Captions are timed evenly from the script.",
            file=sys.stderr,
        )
        return even_captions(text, spoken), "script"
    cues = [
        {"text": word.text.strip(), "start": word.start, "end": word.end}
        for word in words
        if word.text.strip()
    ]
    if not cues:
        return even_captions(text, spoken), "script"
    return cues, "whisper"


def _suffix(url: str) -> str:
    path = url.split("?", 1)[0].lower()
    for ext in (".png", ".webp", ".jpeg", ".jpg"):
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return ".jpg"


def stage_image(document: dict, directory: Path) -> str | None:
    chosen = ken_burns_source(document)
    if chosen is None:
        return None
    filename = "ken-burns" + _suffix(chosen["url"])
    destination = directory / filename
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.get(chosen["url"])
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if content_type and not content_type.startswith("image/"):
                return None
            ensure_parent(destination)
            destination.write_bytes(response.content)
    except (httpx.HTTPError, OSError) as exc:
        print(f"edu-video: skipped Ken Burns image: {exc}", file=sys.stderr)
        return None
    if destination.stat().st_size < 32:
        return None
    return filename


def media_dir(package_path: Path) -> Path:
    return package_path.parent / f"{package_path.stem}-media"


def video_path_for(package_path: Path) -> Path:
    return package_path.with_suffix(".mp4")


def caption_path_for(package_path: Path) -> Path:
    return package_path.with_suffix(".caption.txt")


def prepare_render(package_path: Path) -> tuple[dict, Path, Path]:
    package = json.loads(package_path.read_text(encoding="utf-8"))
    script = package["script"]
    spoken_script = narration_text(script)
    folder = media_dir(package_path)
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    audio = folder / "voice.wav"
    spoken, backend = ensure_audio(spoken_script, audio)
    spoken = spoken_seconds(spoken, backend)
    captions, _caption_backend = ensure_captions(audio, spoken_script, spoken)
    image_file = None
    for source in package.get("sources") or []:
        if isinstance(source, dict):
            image_file = stage_image(source, folder)
            if image_file:
                break
    props = build_props(
        package,
        spoken=spoken,
        audio_file=audio.name,
        image_file=image_file,
        captions=captions,
    )
    props_path = package_path.with_suffix(".props.json")
    props_path.write_text(json.dumps(props, indent=2) + "\n", encoding="utf-8")
    caption_path = caption_path_for(package_path)
    ensure_parent(caption_path)
    caption_path.write_text(post_caption(package), encoding="utf-8")
    return props, props_path, folder


def remotion_command(props_path: Path, public_dir: Path, output: Path) -> list[str]:
    return [
        "npx",
        "remotion",
        "render",
        "src/edu-index.ts",
        "EduReel",
        str(output),
        f"--props={props_path}",
        f"--public-dir={public_dir}",
        "--codec=h264",
        "--concurrency=4",
        "--gl=swangle",
    ]


def render_reel(package_path: Path) -> tuple[Path, Path]:
    _props, props_path, folder = prepare_render(package_path)
    output = video_path_for(package_path)
    ensure_parent(output)
    completed = subprocess.run(
        remotion_command(props_path, folder, output),
        cwd=repo_root() / "core",
        check=False,
    )
    if completed.returncode != 0 or not output.is_file():
        raise RuntimeError(f"Remotion render failed with exit {completed.returncode}")
    return output, caption_path_for(package_path)
