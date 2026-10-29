import os
import unittest
from unittest.mock import patch

from edu.niche import load_niche


class NicheTests(unittest.TestCase):
    def test_default_niche_is_science_and_space(self) -> None:
        with patch.dict(os.environ, {"OLLAMA_HOST": "", "OLLAMA_MODEL": ""}):
            niche = load_niche()
        self.assertEqual(niche.name, "science & space facts")
        self.assertEqual(niche.openstax_query, "astronomy")
        self.assertEqual(
            niche.sources,
            ("wikipedia", "nasa", "openstax", "wikimedia"),
        )
        self.assertEqual(niche.ollama_model, "llama3.2")
        self.assertEqual(niche.length_seconds, 30)

    def test_ollama_env_overrides_yaml(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OLLAMA_HOST": "http://127.0.0.1:9",
                "OLLAMA_MODEL": "fixture-model",
            },
        ):
            niche = load_niche()
        self.assertEqual(niche.name, "science & space facts")
        self.assertEqual(niche.ollama_host, "http://127.0.0.1:9")
        self.assertEqual(niche.ollama_model, "fixture-model")


if __name__ == "__main__":
    unittest.main()
