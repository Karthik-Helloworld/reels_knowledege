import json
import unittest
from datetime import datetime, timezone

import httpx

from edu.niche import NicheConfig
from edu.scripts.script_gen import (
    OLLAMA_ATTEMPTS,
    GroundingError,
    ReelScript,
    build_messages,
    generate_script,
)
from edu.sources.model import SourceDocument
from edu.tests.support import load_fixture

RETRIEVED = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
NICHE = NicheConfig(
    name="science & space facts",
    audience="general viewers",
    tone="clear and concrete",
    length_seconds=30,
    openstax_query="astronomy",
    ollama_host="http://127.0.0.1:11434",
    ollama_model="llama3.2",
    sources=("wikipedia",),
)
DOCUMENT = SourceDocument(
    title="Moon",
    text="The Moon is Earth's only natural satellite.",
    url="https://en.wikipedia.org/wiki/Moon",
    license="CC BY-SA 4.0",
    author="Wikipedia contributors",
    retrieved_at=RETRIEVED,
)


def ollama_client(payload: dict, seen: dict | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def chat_with(script: dict) -> dict:
    return {"message": {"role": "assistant", "content": json.dumps(script)}}


class ScriptGenTests(unittest.TestCase):
    def test_prompt_includes_source_text_and_grounding_rule(self) -> None:
        messages = build_messages(
            "Why the Moon has phases",
            "orbit hint",
            NICHE,
            [DOCUMENT],
        )
        self.assertIn("Use only the source text", messages[0]["content"])
        self.assertIn("cited source sentence", messages[0]["content"])
        self.assertIn("not a source", messages[0]["content"])
        user = messages[1]["content"]
        self.assertIn("The Moon is Earth's only natural satellite.", user)
        self.assertIn("https://en.wikipedia.org/wiki/Moon", user)
        self.assertIn("orbit hint", user)
        self.assertIn("science & space facts", user)
        self.assertIn("non-empty", messages[0]["content"])
        self.assertIn("non-empty", user)
        self.assertGreaterEqual(
            ReelScript.model_json_schema()["properties"]["closing"]["minLength"],
            1,
        )

    def test_fixture_response_is_grounded(self) -> None:
        script = load_fixture("ollama_script.json")
        seen: dict = {}
        with ollama_client(chat_with(script), seen) as client:
            result = generate_script(
                "Why the Moon has phases",
                "The lit portion we see changes as the Moon orbits Earth",
                NICHE,
                [DOCUMENT],
                client,
            )
        self.assertEqual(result.topic, "Why the Moon has phases")
        self.assertEqual(result.beats[0].source_url, DOCUMENT.url)
        self.assertEqual(result.hook, script["hook"])
        self.assertTrue(seen["url"].endswith("/api/chat"))
        self.assertEqual(seen["body"]["model"], "llama3.2")
        self.assertEqual(seen["body"]["format"]["type"], "object")
        self.assertIn("claims", seen["body"]["format"]["required"])
        self.assertIs(seen["body"]["stream"], False)
        self.assertIn(DOCUMENT.text, seen["body"]["messages"][1]["content"])

    def test_fenced_json_still_parses(self) -> None:
        script = load_fixture("ollama_script.json")
        content = "```json\n" + json.dumps(script) + "\n```"
        payload = {"message": {"role": "assistant", "content": content}}
        with ollama_client(payload) as client:
            result = generate_script(
                "Why the Moon has phases",
                "",
                NICHE,
                [DOCUMENT],
                client,
            )
        self.assertEqual(result.closing, script["closing"])

    def test_unknown_source_url_is_rejected(self) -> None:
        script = load_fixture("ollama_script.json")
        script["beats"][0]["source_url"] = "https://example.com/not-a-source"
        with ollama_client(chat_with(script)) as client:
            with self.assertRaises(GroundingError) as caught:
                generate_script(
                    "Why the Moon has phases",
                    "",
                    NICHE,
                    [DOCUMENT],
                    client,
                )
        self.assertIn("not-a-source", str(caught.exception))

    def test_topic_mismatch_is_rejected(self) -> None:
        script = load_fixture("ollama_script.json")
        script["topic"] = "Something else"
        with ollama_client(chat_with(script)) as client:
            with self.assertRaises(GroundingError):
                generate_script(
                    "Why the Moon has phases",
                    "",
                    NICHE,
                    [DOCUMENT],
                    client,
                )

    def test_invalid_json_is_rejected(self) -> None:
        payload = {"message": {"role": "assistant", "content": "not-json"}}
        with ollama_client(payload) as client:
            with self.assertRaises(GroundingError):
                generate_script(
                    "Why the Moon has phases",
                    "",
                    NICHE,
                    [DOCUMENT],
                    client,
                )

    def test_empty_sources_do_not_call_ollama(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("Ollama should not be called")

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(GroundingError):
                generate_script("Why the Moon has phases", "", NICHE, [], client)

    def test_source_sentence_must_be_in_the_cited_text(self) -> None:
        script = load_fixture("ollama_script.json")
        script["claims"][0]["source_sentence"] = "The Moon controls the tides."
        with ollama_client(chat_with(script)) as client:
            with self.assertRaises(GroundingError) as caught:
                generate_script(
                    "Why the Moon has phases",
                    "",
                    NICHE,
                    [DOCUMENT],
                    client,
                )
        self.assertIn("source_sentence", str(caught.exception))

    def test_source_sentence_ignores_extra_whitespace(self) -> None:
        script = load_fixture("ollama_script.json")
        script["claims"][0]["source_sentence"] = "The   Moon is Earth's only natural satellite."
        with ollama_client(chat_with(script)) as client:
            result = generate_script(
                "Why the Moon has phases",
                "",
                NICHE,
                [DOCUMENT],
                client,
            )
        self.assertEqual(
            result.claims[0].source_sentence,
            "The Moon is Earth's only natural satellite.",
        )

    def test_hook_longer_than_140_characters_is_rejected(self) -> None:
        script = load_fixture("ollama_script.json")
        script["hook"] = "A" * 141
        with ollama_client(chat_with(script)) as client:
            with self.assertRaises(GroundingError) as caught:
                generate_script(
                    "Why the Moon has phases",
                    "",
                    NICHE,
                    [DOCUMENT],
                    client,
                )
        self.assertIn("140", str(caught.exception))

    def test_malformed_json_is_retried(self) -> None:
        calls = {"n": 0}
        script = load_fixture("ollama_script.json")

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                content = "not-json"
            else:
                content = json.dumps(script)
            return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = generate_script(
                "Why the Moon has phases",
                "",
                NICHE,
                [DOCUMENT],
                client,
            )
        self.assertEqual(calls["n"], 2)
        self.assertEqual(result.claims[0].source_url, DOCUMENT.url)

    def test_words_outside_the_cited_sentence_are_rejected(self) -> None:
        script = load_fixture("ollama_script.json")
        script["beats"][0]["narration"] = "Imagine the Moon is Earth's only natural satellite."
        with ollama_client(chat_with(script)) as client:
            with self.assertRaises(GroundingError) as caught:
                generate_script(
                    "Why the Moon has phases",
                    "",
                    NICHE,
                    [DOCUMENT],
                    client,
                )
        self.assertIn("Imagine", str(caught.exception))

    def test_empty_closing_is_retried_and_not_accepted(self) -> None:
        script = load_fixture("ollama_script.json")
        calls = {"n": 0}
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            seen["body"] = json.loads(request.content.decode())
            body = dict(script)
            body["closing"] = ""
            return httpx.Response(
                200,
                json={"message": {"role": "assistant", "content": json.dumps(body)}},
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(GroundingError) as caught:
                generate_script(
                    "Why the Moon has phases",
                    "",
                    NICHE,
                    [DOCUMENT],
                    client,
                )
        self.assertEqual(calls["n"], OLLAMA_ATTEMPTS)
        self.assertIn("closing", str(caught.exception).casefold())
        self.assertIn("non-empty", seen["body"]["messages"][-1]["content"])

    def test_empty_closing_retries_until_a_sentence_is_present(self) -> None:
        script = load_fixture("ollama_script.json")
        calls = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            body = dict(script)
            if calls["n"] == 1:
                body["closing"] = " "
            return httpx.Response(
                200,
                json={"message": {"role": "assistant", "content": json.dumps(body)}},
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = generate_script(
                "Why the Moon has phases",
                "",
                NICHE,
                [DOCUMENT],
                client,
            )
        self.assertEqual(calls["n"], 2)
        self.assertEqual(result.closing, script["closing"])


if __name__ == "__main__":
    unittest.main()
