import tempfile
import unittest
from pathlib import Path

from core.utils import VIDEO_FPS, VIDEO_HEIGHT, VIDEO_WIDTH, ensure_parent, repo_root


class UtilsTests(unittest.TestCase):
    def test_video_constants_match_theme(self) -> None:
        self.assertEqual(VIDEO_WIDTH, 1080)
        self.assertEqual(VIDEO_HEIGHT, 1920)
        self.assertEqual(VIDEO_FPS, 30)

    def test_repo_root_contains_readme(self) -> None:
        self.assertTrue((repo_root() / "README.md").is_file())

    def test_ensure_parent_creates_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "clip.wav"
            ensure_parent(target)
            self.assertTrue(target.parent.is_dir())
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
