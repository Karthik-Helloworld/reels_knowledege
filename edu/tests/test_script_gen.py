import json
import unittest
from datetime import datetime, timezone

import httpx

from edu.niche import NicheConfig
from edu.scripts.fact_check import fact_check
from edu.scripts.script_gen import (
    OLLAMA_ATTEMPTS,
    VERBATIM_CORRECTION,
    GroundingError,
    ReelScript,
    build_messages,
    copies_cited_sentence,
    generate_script,
    sentence_in_source,
    unsupported_words,
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
        self.assertIn("numbered source sentences", messages[0]["content"])
        self.assertIn("sentence_ids", messages[0]["content"])
        self.assertIn("own words", messages[0]["content"])
        self.assertIn("not a source", messages[0]["content"])
        user = messages[1]["content"]
        self.assertIn("The Moon is Earth's only natural satellite.", user)
        self.assertIn("https://en.wikipedia.org/wiki/Moon", user)
        self.assertIn("orbit hint", user)
        self.assertIn("science & space facts", user)
        self.assertIn("non-empty", messages[0]["content"])
        self.assertIn("sentence_ids", user)
        self.assertIn('"id": "1"', user)
        schema = ReelScript.model_json_schema()
        for name in ("topic", "hook", "closing", "key_takeaway", "cta"):
            self.assertGreaterEqual(schema["properties"][name]["minLength"], 1)
        beat = schema["$defs"]["ScriptBeat"]["properties"]
        claim = schema["$defs"]["ScriptClaim"]["properties"]
        for name in beat:
            self.assertGreaterEqual(beat[name]["minLength"], 1)
        for name in claim:
            self.assertGreaterEqual(claim[name]["minLength"], 1)

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
        claim_schema = seen["body"]["format"]["$defs"]["DraftClaim"]["properties"]
        self.assertIn("sentence_ids", claim_schema)
        self.assertNotIn("source_sentence", claim_schema)
        self.assertGreaterEqual(claim_schema["sentence_ids"]["minItems"], 1)
        self.assertIs(seen["body"]["stream"], False)
        self.assertIn(DOCUMENT.text, seen["body"]["messages"][1]["content"])
        self.assertEqual(
            result.claims[0].source_sentence,
            "The Moon is Earth's only natural satellite.",
        )

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

    def test_fabricated_sentence_id_is_rejected(self) -> None:
        script = load_fixture("ollama_script.json")
        script["claims"][0]["sentence_ids"] = ["99"]
        with ollama_client(chat_with(script)) as client:
            with self.assertRaises(GroundingError) as caught:
                generate_script(
                    "Why the Moon has phases",
                    "",
                    NICHE,
                    [DOCUMENT],
                    client,
                )
        self.assertIn("99", str(caught.exception))

    def test_claim_with_no_sentence_id_is_rejected(self) -> None:
        script = load_fixture("ollama_script.json")
        script["claims"][0]["sentence_ids"] = []
        with ollama_client(chat_with(script)) as client:
            with self.assertRaises(GroundingError) as caught:
                generate_script(
                    "Why the Moon has phases",
                    "",
                    NICHE,
                    [DOCUMENT],
                    client,
                )
        self.assertIn("sentence", str(caught.exception).casefold())

    def test_valid_sentence_id_copies_the_exact_source_sentence(self) -> None:
        text = "Alpha is first. Beta is second."
        script = load_fixture("ollama_script.json")
        script["hook"] = "Here is the second line."
        script["closing"] = "That was the second line."
        script["key_takeaway"] = "Beta comes second."
        script["beats"][0]["narration"] = "The second line names Beta."
        script["beats"][0]["on_screen"] = "Beta"
        script["claims"][0]["text"] = "Beta is the second sentence."
        script["claims"][0]["sentence_ids"] = ["2"]
        document = SourceDocument(
            title="Moon",
            text=text,
            url=DOCUMENT.url,
            license=DOCUMENT.license,
            author=DOCUMENT.author,
            retrieved_at=RETRIEVED,
        )
        with ollama_client(chat_with(script)) as client:
            result = generate_script(
                "Why the Moon has phases",
                "",
                NICHE,
                [document],
                client,
            )
        self.assertEqual(result.claims[0].source_sentence, "Beta is second.")
        self.assertNotEqual(result.claims[0].text, result.claims[0].source_sentence)

    def test_black_hole_sentence_matches_the_lowercase_extract(self) -> None:
        extract = (
            "Albert Einstein's theory of general relativity, which describes "
            "gravitation as the curvature of spacetime, predicts that any "
            "sufficiently compact mass will form a black hole."
        )
        sentence = "Any sufficiently compact mass will form a black hole."
        self.assertTrue(sentence_in_source(sentence, extract))
        script = load_fixture("ollama_script.json")
        script["topic"] = "Black hole"
        script["hook"] = sentence
        script["closing"] = sentence
        script["key_takeaway"] = sentence
        script["beats"][0]["narration"] = sentence
        script["beats"][0]["on_screen"] = "black hole"
        script["beats"][0]["source_url"] = "https://en.wikipedia.org/wiki/Black_hole"
        script["claims"][0]["text"] = "A compact mass can become a black hole."
        script["claims"][0]["source_url"] = "https://en.wikipedia.org/wiki/Black_hole"
        script["claims"][0]["sentence_ids"] = ["1"]
        document = SourceDocument(
            title="Black hole",
            text=extract,
            url="https://en.wikipedia.org/wiki/Black_hole",
            license="CC BY-SA 4.0",
            author="Wikipedia contributors",
            retrieved_at=RETRIEVED,
        )
        with ollama_client(chat_with(script)) as client:
            result = generate_script("Black hole", "", NICHE, [document], client)
        self.assertEqual(result.claims[0].source_sentence, extract)
        self.assertNotEqual(result.claims[0].text, extract)

    def test_original_wording_can_differ_from_the_cited_sentence(self) -> None:
        extract = (
            "predicts that any sufficiently compact mass will form a black hole."
        )
        sentence = "Any sufficiently compact mass will form a black hole."
        script = load_fixture("ollama_script.json")
        script["topic"] = "Black hole"
        script["hook"] = sentence
        script["closing"] = sentence
        script["key_takeaway"] = sentence
        script["beats"][0]["narration"] = sentence
        script["beats"][0]["on_screen"] = "black hole"
        script["beats"][0]["source_url"] = "https://en.wikipedia.org/wiki/Black_hole"
        script["claims"][0]["text"] = "Any sufficiently compact mass will form a wormhole."
        script["claims"][0]["source_url"] = "https://en.wikipedia.org/wiki/Black_hole"
        script["claims"][0]["sentence_ids"] = ["1"]
        document = SourceDocument(
            title="Black hole",
            text=extract,
            url="https://en.wikipedia.org/wiki/Black_hole",
            license="CC BY-SA 4.0",
            author="Wikipedia contributors",
            retrieved_at=RETRIEVED,
        )
        with ollama_client(chat_with(script)) as client:
            result = generate_script("Black hole", "", NICHE, [document], client)
        self.assertEqual(result.claims[0].source_sentence, extract)
        self.assertIn("wormhole", result.claims[0].text)

    def test_articles_are_ignored_and_other_words_must_be_cited(self) -> None:
        support = "sufficiently compact mass will form black hole"
        self.assertEqual(unsupported_words("A black hole will form.", support), [])
        self.assertEqual(unsupported_words("An black hole will form.", support), [])
        self.assertEqual(unsupported_words("The black hole will form.", support), [])
        self.assertEqual(unsupported_words("A wormhole will form.", support), ["wormhole"])
        sentence = "Sufficiently compact mass will form black hole."
        script = load_fixture("ollama_script.json")
        script["topic"] = "Black hole"
        script["hook"] = "A black hole will form."
        script["closing"] = sentence
        script["key_takeaway"] = sentence
        script["beats"][0]["narration"] = sentence
        script["beats"][0]["on_screen"] = "black hole"
        script["beats"][0]["source_url"] = "https://en.wikipedia.org/wiki/Black_hole"
        script["claims"][0]["text"] = sentence
        script["claims"][0]["source_url"] = "https://en.wikipedia.org/wiki/Black_hole"
        script["claims"][0]["sentence_ids"] = ["1"]
        document = SourceDocument(
            title="Black hole",
            text=sentence.casefold(),
            url="https://en.wikipedia.org/wiki/Black_hole",
            license="CC BY-SA 4.0",
            author="Wikipedia contributors",
            retrieved_at=RETRIEVED,
        )
        with ollama_client(chat_with(script)) as client:
            result = generate_script("Black hole", "", NICHE, [document], client)
        self.assertEqual(result.hook, "A black hole will form.")

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

    def test_original_wording_is_kept_and_the_citation_stays_exact(self) -> None:
        script = load_fixture("ollama_script.json")
        script["beats"][0]["narration"] = "Imagine the Moon is Earth's only natural satellite."
        with ollama_client(chat_with(script)) as client:
            result = generate_script(
                "Why the Moon has phases",
                "",
                NICHE,
                [DOCUMENT],
                client,
            )
        self.assertIn("Imagine", result.beats[0].narration)
        self.assertEqual(result.claims[0].source_sentence, DOCUMENT.text)
        self.assertNotIn("Imagine", result.claims[0].source_sentence)

    def test_a_paraphrase_is_not_treated_as_a_copy(self) -> None:
        sentence = "The Moon is Earth's only natural satellite."
        self.assertTrue(copies_cited_sentence(sentence, sentence))
        self.assertTrue(
            copies_cited_sentence(
                "Imagine the Moon is Earth's only natural satellite.",
                sentence,
            )
        )
        self.assertFalse(copies_cited_sentence("The Moon orbits Earth.", sentence))
        self.assertFalse(
            copies_cited_sentence("Earth has one natural satellite.", sentence)
        )

    def test_verbatim_copy_is_retried_then_a_paraphrase_is_kept(self) -> None:
        sentence = DOCUMENT.text
        copied = load_fixture("ollama_script.json")
        copied["hook"] = sentence
        copied["beats"][0]["narration"] = sentence
        copied["closing"] = sentence
        copied["key_takeaway"] = sentence
        copied["claims"][0]["text"] = sentence
        paraphrase = load_fixture("ollama_script.json")
        calls = {"n": 0}
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            body = json.loads(request.content.decode())
            seen.append(body["messages"][-1]["content"])
            payload = copied if calls["n"] == 1 else paraphrase
            return httpx.Response(
                200,
                json={"message": {"role": "assistant", "content": json.dumps(payload)}},
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
        self.assertIn(VERBATIM_CORRECTION, seen[1])
        self.assertEqual(result.hook, "The Moon orbits Earth.")
        self.assertNotEqual(result.hook, result.claims[0].source_sentence)
        self.assertNotEqual(result.claims[0].text, result.claims[0].source_sentence)

    def test_verbatim_copy_is_kept_when_every_attempt_repeats_it(self) -> None:
        sentence = DOCUMENT.text
        copied = load_fixture("ollama_script.json")
        copied["hook"] = sentence
        copied["beats"][0]["narration"] = sentence
        copied["claims"][0]["text"] = sentence
        calls = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(
                200,
                json={"message": {"role": "assistant", "content": json.dumps(copied)}},
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = generate_script(
                "Why the Moon has phases",
                "",
                NICHE,
                [DOCUMENT],
                client,
            )
        self.assertEqual(calls["n"], OLLAMA_ATTEMPTS)
        self.assertEqual(result.hook, sentence)
        self.assertEqual(result.claims[0].source_sentence, sentence)

    def test_tampered_date_still_blocks(self) -> None:
        script = load_fixture("ollama_script.json")
        script["beats"][0]["narration"] = "On April 2, 1999 the Moon orbits Earth."
        script["claims"][0]["text"] = "The Moon was measured on April 2, 1999."
        with ollama_client(chat_with(script)) as client:
            result = generate_script(
                "Why the Moon has phases",
                "",
                NICHE,
                [DOCUMENT],
                client,
            )
        self.assertEqual(result.claims[0].source_sentence, DOCUMENT.text)
        self.assertNotIn("1999", result.claims[0].source_sentence)
        report = fact_check(
            result,
            [DOCUMENT],
            niche=NICHE,
            llm_payload=load_fixture("fact_llm_pass.json"),
        )
        self.assertFalse(report.passed)
        self.assertTrue(report.blocked)
        beat = next(claim for claim in report.claims if claim.id == "beat-0")
        self.assertIn("date not in source: April 2, 1999", beat.rule_failures)
        self.assertFalse(beat.passed)

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
        self.assertIn("key_takeaway", seen["body"]["messages"][-1]["content"])

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
