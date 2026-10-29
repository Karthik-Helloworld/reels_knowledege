import tempfile
import unittest
from pathlib import Path

from core.captions import (
    CaptionsUnavailable,
    WordCaption,
    format_srt_timestamp,
    transcribe,
    whisper_available,
    words_to_srt,
    write_srt,
)


class CaptionTests(unittest.TestCase):
    def test_srt_timestamp_and_cues(self) -> None:
        self.assertEqual(format_srt_timestamp(1.25), "00:00:01,250")
        self.assertEqual(format_srt_timestamp(3661.009), "01:01:01,009")
        srt = words_to_srt(
            [
                WordCaption(" Hello ", 0.0, 0.4),
                WordCaption("   ", 0.4, 0.5),
                WordCaption("world", 0.5, 1.0),
            ]
        )
        self.assertEqual(
            srt,
            "1\n00:00:00,000 --> 00:00:00,400\nHello\n\n"
            "2\n00:00:00,500 --> 00:00:01,000\nworld\n",
        )

    def test_write_srt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "caps" / "words.srt"
            write_srt([WordCaption("Hi", 0, 0.2)], path)
            self.assertIn("Hi", path.read_text(encoding="utf-8"))

    def test_missing_audio(self) -> None:
        with self.assertRaises(FileNotFoundError):
            transcribe("/tmp/does-not-exist-phase0.wav")

    def test_missing_whisper_is_explicit(self) -> None:
        self.assertFalse(whisper_available())
        with tempfile.NamedTemporaryFile(suffix=".wav") as handle:
            with self.assertRaises(CaptionsUnavailable) as caught:
                transcribe(handle.name)
        self.assertIn("faster-whisper", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
