import random
import tempfile
import unittest
from pathlib import Path

from edu.scripts.topic_picker import load_topics, mark_topic_used, pick_topic


class TopicPickerTests(unittest.TestCase):
    def test_catalog_includes_moon_phases(self) -> None:
        topics = load_topics()
        match = [item for item in topics if item.topic == "Why the Moon has phases"]
        self.assertEqual(len(match), 1)
        self.assertIn("Moon", match[0].angle)

    def test_explicit_topic_keeps_the_csv_angle(self) -> None:
        choice = pick_topic("why the moon has phases")
        self.assertEqual(choice.topic, "Why the Moon has phases")
        self.assertTrue(choice.angle)

    def test_unknown_topic_is_allowed(self) -> None:
        choice = pick_topic("A custom topic")
        self.assertEqual(choice.topic, "A custom topic")
        self.assertEqual(choice.angle, "")

    def test_blank_topic_follows_the_rng(self) -> None:
        first = pick_topic("", rng=random.Random(0))
        second = pick_topic(None, rng=random.Random(0))
        self.assertEqual(first, second)
        known = {item.topic for item in load_topics()}
        self.assertIn(first.topic, known)

    def test_blank_topic_skips_used_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "topics.csv"
            path.write_text(
                "topic,angle,status\n"
                "Used topic,done already,used\n"
                "Open topic,still waiting,pending\n",
                encoding="utf-8",
            )
            choice = pick_topic("", path, rng=random.Random(0))
            self.assertEqual(choice.topic, "Open topic")
            self.assertEqual(choice.status, "pending")

    def test_mark_topic_used_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "topics.csv"
            path.write_text(
                "topic,angle,status\nOpen topic,still waiting,pending\n",
                encoding="utf-8",
            )
            self.assertTrue(mark_topic_used("Open topic", path))
            self.assertEqual(load_topics(path)[0].status, "used")
            with self.assertRaises(ValueError):
                pick_topic("", path)

    def test_empty_csv_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "topics.csv"
            path.write_text("topic,angle\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_topics(path)


if __name__ == "__main__":
    unittest.main()
