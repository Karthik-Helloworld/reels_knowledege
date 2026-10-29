# reels_knowledege

Local educational Reels and Shorts pipeline.

Phase 0 is a Remotion core that renders one short hello-world video. Phase 1 picks a topic, fetches sources, and writes a grounded script. Phase 2 checks that script against the cited sources and blocks the video when a claim fails. Nothing here renders an educational scene, reviews, or publishes.

## What exists

- `core/` Remotion project: TypeScript, 1080×1920, 30fps. One composition, `HelloWorld` (4 seconds).
- `core/src/theme.ts` colors, fonts, and spacing. Fonts are system stacks, so render does not download typefaces.
- `core/src/utils.ts` and `core/utils.py` shared helpers. Python video constants match the theme.
- `core/tts.py` Piper TTS, with edge-tts as an optional fallback. If neither is installed, `synthesize` raises `TTSUnavailable`.
- `core/captions.py` word-level captions through faster-whisper, plus SRT helpers. If faster-whisper is missing, `transcribe` raises `CaptionsUnavailable`.
- `.env.example` commented placeholder names only. Nothing in Phase 0 reads them.

Piper and faster-whisper are not installed by `make edu-setup`. The hello-world render does not call them.

## Install

From the repo root, with Node 20+ and Python 3.11+. On Debian or Ubuntu, `python3 -m venv` needs the `python3-venv` package (`sudo apt install python3.12-venv`).

```bash
make edu-setup
```

That creates `.venv`, installs `core/requirements.txt` (no model runtimes), runs `npm install` in `core/`, and downloads Remotion's Chrome headless shell.

## Render the hello-world video

```bash
make edu-render-hello
```

Same command from `core/`: `npm run render:hello`.

Output: `core/out/hello-world.mp4`.

The render uses one Chrome process and software GL so it can run without a GPU.

## Tests

```bash
make edu-test
```

Python tests cover TTS, captions, and the theme constants in `core/utils.py`. `make edu-test` also runs the Phase 1 and Phase 2 tests and typechecks the Remotion project. The Phase 2 tests use fixtures and do not call Ollama.

## Phase 1: topic, sources, and a grounded script

The default niche in `edu/config/niche.yaml` is `science & space facts`.

`make edu-video` resolves a topic, fetches one record from each source, and asks local Ollama for a short JSON script. The script includes a hook of at most 140 characters, a key takeaway, a call to action, and `claims`. Each claim has `text`, `source_url`, and `source_sentence`. `source_sentence` has to be copied from the cited source text. The angle in `edu/topics.csv` is a hint, not a source. `topics.csv` has a `status` column. A blank topic picks a `pending` row, and a successful package marks that row `used`. Phase 2 then fact-checks that script before the package is kept.

Sources, each stored as title, text, url, license, author, and retrieved_at:

- Wikipedia, found by search, then the REST summary. Article text is `CC BY-SA 4.0`.
- NASA Image and Video Library. No API key. The license is the asset's own usage terms. Only public-domain and CC0 items are kept.
- OpenStax books. The text is the book description. If the topic matches no title, the niche searches `astronomy`.
- Wikimedia Commons. License and author come from the file metadata. Files are kept only when the license allows reuse (`CC0`, `CC BY`, `CC BY-SA`, or public domain).

If one source is down, the others are still used. A source HTTP call retries twice with backoff on connection errors and 5xx responses. A bad Ollama JSON response is retried twice before the run fails.

```bash
make edu-setup
make edu-video TOPIC="Why the Moon has phases"
```

`make edu-setup` also installs `edu/requirements.txt`. Leave `TOPIC` empty to let `edu/scripts/topic_picker.py` choose a row from `edu/topics.csv`.

Output: `edu/out/<topic>.json` with the sources and the script. That directory is gitignored. Ollama has to be running locally. The host and model come from the niche file, or from `OLLAMA_HOST` and `OLLAMA_MODEL` in the environment. This file is not loaded for you.

```bash
ollama serve
ollama pull llama3.2
```

`make edu-test` covers the source adapters with mock HTTP and the script generator with an Ollama fixture. Those tests do not call the network or Ollama.

`make edu-video` does not render a video. The hello-world render is still `make edu-render-hello`.

## Phase 2: fact check

After script generation, `make edu-video` runs `edu/scripts/fact_check.py`. It is a second Ollama pass on the same model as script generation (`edu/config/niche.yaml`, or `OLLAMA_HOST` / `OLLAMA_MODEL`). A bad JSON verdict is retried twice before the run fails. The model judges each claim against one source at a time. A beat uses its `source_url`. The hook and the closing are checked against each source the beats cite, separately. One of those sources has to support the whole claim. Pooling the sources together does not count.

Rule checks run on that same single source. Every number, date, and name in the claim must appear in it. Commas in numbers are ignored, so `384,400` matches `384400`. A month-and-day date has to appear by its month name and its numbers. A proper name has to appear as that name. The name check is a capitalized-word heuristic, so an ordinary capitalized noun can block a claim the model would accept. Both the model and the rules have to pass.

If any claim fails, the command exits non-zero and prints the claim id with the reason. It still writes `fact_report.json` (by default `edu/out/fact_report.json`, next to the package path). It does not write the video package, so a failed check cannot be mistaken for a finished script.

```bash
make edu-video TOPIC="Why the Moon has phases"
```

Dry run, with no live model. This reads a saved package and a canned verdict, writes the report, and exits 0 only when every claim passes:

```bash
python -m edu.scripts.fact_check \
  --package edu/tests/fixtures/fact_package_pass.json \
  --llm-fixture edu/tests/fixtures/fact_llm_pass.json \
  --report edu/out/fact_report.json
```

Swap in `edu/tests/fixtures/fact_package_low_support.json` and `edu/tests/fixtures/fact_llm_low_support.json` to see a low-support failure. Swap in `edu/tests/fixtures/fact_package_rules.json` with `fact_llm_pass.json` to see a number, a date, and a name that are not in the source.

To generate the script with Ollama but fact-check from a fixture:

```bash
make edu-video TOPIC="Why the Moon has phases" FACT_FIXTURE=edu/tests/fixtures/fact_llm_pass.json
```

`make edu-test` covers the pass fixture, the low-support fixture, the missing number/date/name fixture, a hook that mixes facts from two sources, a retried bad verdict, and a failed claim that stops `edu-video` before it writes the package. Those tests do not call Ollama. A missing `FACT_FIXTURE` path exits with `fact-check fixture not found` and the path.

Phase 2 does not render an educational scene, review, or publish.

## Mac install (MacBook Air M4, 16GB)

Phase 0 was checked on a Linux cloud VM, where Piper and faster-whisper are stubbed behind the interfaces above. On the Mac, install the tools, then the same make targets:

- Python 3.11
- Node.js 20 or newer
- ffmpeg: `brew install ffmpeg`
- `make edu-setup`
- `make edu-render-hello`

Speech and captions, when you want them:

```bash
.venv/bin/pip install piper-tts edge-tts faster-whisper
.venv/bin/python -m piper.download_voices en_US-lessac-medium
export PIPER_MODEL=/path/to/en_US-lessac-medium.onnx
```

`download_voices` writes `en_US-lessac-medium.onnx` (and its `.json`) into the current directory. Pass `--data-dir` to put them somewhere else. `PIPER_MODEL` must point at that `.onnx` file.

Piper is the primary backend. edge-tts runs only when Piper is not ready; it needs a network at synthesis time. Captions default to faster-whisper `base` on CPU int8 (`WHISPER_MODEL`, `WHISPER_DEVICE`, `WHISPER_COMPUTE_TYPE`). A standalone Piper 1.2 binary also works: set `PIPER_BIN` and `PIPER_MODEL`. Text is sent on stdin with `--model` and `--output_file`.
