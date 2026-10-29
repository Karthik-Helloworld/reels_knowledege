import os
import stat
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from core.tts import (
    TTSUnavailable,
    available_backends,
    build_piper_cli_command,
    select_backend,
    synthesize,
)


FAKE_PIPER = """#!/usr/bin/env python3
import sys
import wave

args = sys.argv[1:]
out = None
index = 0
while index < len(args):
    if args[index] == "--output_file":
        out = args[index + 1]
        index += 2
        continue
    index += 1
if not sys.stdin.buffer.read().strip():
    sys.stderr.write("empty text\\n")
    sys.exit(2)
if not out:
    sys.stderr.write("missing --output_file\\n")
    sys.exit(2)
with wave.open(out, "wb") as handle:
    handle.setnchannels(1)
    handle.setsampwidth(2)
    handle.setframerate(16000)
    handle.writeframes(b"\\x00\\x00" * 1600)
"""


class TTSInterfaceTests(unittest.TestCase):
    def test_no_backend_raises_with_install_hint(self) -> None:
        with patch("core.tts.piper_python_ready", return_value=False), patch(
            "core.tts.piper_cli_ready", return_value=False
        ), patch("core.tts.edge_tts_available", return_value=False):
            self.assertEqual(available_backends(), [])
            with self.assertRaises(TTSUnavailable) as caught:
                select_backend(None)
        message = str(caught.exception)
        self.assertIn("Piper", message)
        self.assertIn("edge-tts", message)

    def test_unknown_backend(self) -> None:
        with self.assertRaises(TTSUnavailable):
            select_backend("festival")

    def test_empty_text(self) -> None:
        with self.assertRaises(ValueError):
            synthesize("   ", "out.wav")

    def test_piper_cli_command_includes_speaker(self) -> None:
        command = build_piper_cli_command(
            "piper", "voice.onnx", "out.wav", speaker="3"
        )
        self.assertEqual(
            command,
            ["piper", "--model", "voice.onnx", "--output_file", "out.wav", "--speaker", "3"],
        )

    def test_prefers_piper_over_edge(self) -> None:
        with patch("core.tts.piper_python_ready", return_value=True), patch(
            "core.tts.edge_tts_available", return_value=True
        ):
            self.assertEqual(select_backend(None), "piper")
            self.assertEqual(available_backends(), ["piper", "edge-tts"])

    def test_piper_cli_writes_wav(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "voice.onnx"
            model.write_bytes(b"placeholder")
            binary = root / "piper"
            binary.write_text(FAKE_PIPER, encoding="utf-8")
            binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
            out = root / "nested" / "hello.wav"
            env = {"PIPER_MODEL": str(model), "PIPER_BIN": str(binary)}
            with patch.dict(os.environ, env, clear=False), patch(
                "core.tts.piper_python_ready", return_value=False
            ):
                result = synthesize("hello there", out)
            self.assertEqual(result.backend, "piper")
            self.assertTrue(out.is_file())
            self.assertAlmostEqual(result.duration_sec or 0, 0.1, places=2)
            with wave.open(str(out), "rb") as handle:
                self.assertEqual(handle.getnchannels(), 1)


if __name__ == "__main__":
    unittest.main()
