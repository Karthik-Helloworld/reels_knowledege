# reels_knowledege

Local educational Reels and Shorts pipeline.

Phase 0 is a Remotion core that renders one short hello-world video. Phase 1 picks a topic, fetches sources, and writes a grounded script. Phase 2 checks that script against the cited sources and blocks the video when a claim fails. Phase 3 renders that script with the same Remotion engine. Nothing here reviews or publishes.

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

Python tests cover TTS, captions, and the theme constants in `core/utils.py`. `make edu-test` also runs the Phase 1, Phase 2, and Phase 3 tests and typechecks the Remotion project. Those tests use fixtures and do not call Ollama or render a video.

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

## Phase 3: render the reel

`make edu-video` fact-checks the script, then renders one vertical video with the Remotion project in `core/`. The scenes live in `edu/remotion` and import `core/src/theme.ts`. They do not copy the theme, the renderer, TTS, or captions.

Scenes, in order: Hook, Concept, Diagram, Timeline, Stat, call to action, then Source credit. The credit card is the last 2 seconds and reads `Source: <author>, <license>`. Word-by-word captions sit above the bottom 250px and left of the right 120px. The file is 1080×1920, 30fps, H.264. Its length is the audio length plus those 2 seconds.

Speech uses `core/tts.py` (Piper, then edge-tts). Captions use `core/captions.py` (faster-whisper). If those packages are missing, the render still finishes: a 36 second generated tone stands in for speech, and captions are spaced evenly across the script. 36 seconds of tone plus the 2 second credit is 38 seconds, inside the 30-45 second target. Real speech shorter than 30 seconds is padded with silence to 36 seconds. Real speech longer than 45 seconds is kept whole.

Visuals are drawn in code. A Wikimedia Commons or NASA still is the one exception, and only when that asset's license allows reuse (Commons: CC0, CC BY, CC BY-SA, or public domain; NASA: public domain or CC0). That still gets a slow Ken Burns move on the diagram scene.

The render also writes a caption file next to the mp4. It is the post text, not an upload. Each source is listed with title, URL, license, and author, then 5 to 8 hashtags.

```bash
make edu-video TOPIC="Black hole"
```

If fetch or Ollama is down, the command says so, fact-checks `edu/tests/fixtures/render_package.json` with `edu/tests/fixtures/render_llm_pass.json`, and renders that package. A failed fact check on a live script still stops the run and does not render. The fixture path does not mark a row in `edu/topics.csv` as used. No publisher login is required.

Output, for that topic: `edu/out/black-hole.mp4` and `edu/out/black-hole.caption.txt`. The same render is also copied into `edu/out/pending/` for review. `edu/out/` is gitignored.

## Phase 4: review and the daily queue

A finished render is not publishable. `make edu-video` copies it into `edu/out/pending/<id>/`:

- `video.mp4`
- `script.json`
- `claims.json`
- `fact_report.json`
- `caption.txt`

`make review` prints each pending item (the video path, the script, the claims, the fact report, and the caption) and asks you to approve, reject, or edit. Approve moves the bundle to `edu/out/approved/<id>/` and inserts a row in `edu/out/queue.sqlite` with `scheduled_at`. Reject moves it to `edu/out/rejected/<id>/` and does not insert a row. Edit changes `caption.txt` and leaves the video pending. Pending and rejected videos fail `can_publish`. This phase does not post to Instagram or YouTube.

The queue schedules one approved video per day by default (`cadence_per_day: 1` in `edu/config/niche.yaml`). The first approval is scheduled at approval time. The next one waits 24 hours after the previous `scheduled_at`. Set `REVIEW_CADENCE_PER_DAY` or pass `--cadence-per-day` to change that. Two per day spaces approvals 12 hours apart.

Without a TTY, pass `--decide`. Tests use that flag and never prompt.

```bash
make review
make review ARGS="--decide approve --id black-hole"
make review ARGS="--decide reject --id black-hole"
make review ARGS="--decide edit --id black-hole --caption 'Revised caption'"
```

## Phase 5: dry-run publish

`make publish` posts due rows from `edu/out/queue.sqlite`. A platform stays a dry-run until its credentials exist, and a dry-run does not upload the mp4 or call that API. `--dry-run` forces that plan even after `.env` is filled in. Nothing pending or rejected is eligible. A fact report that did not pass is skipped. One run publishes at most the daily cadence (`cadence_per_day` in `edu/config/niche.yaml`, default 1). Extra due videos wait for the next run.

Instagram uses the Graph API. The flow is: upload the approved mp4 to a public URL, create a Reels container with that `video_url`, poll `status_code` until `FINISHED`, then `media_publish`. The hosted file is deleted after the attempt. Set `VIDEO_HOST` to `r2` (Cloudflare R2) or `supabase` (Supabase Storage). YouTube uses the Data API v3 resumable upload. The title is at most 100 characters and ends with `#Shorts`. The description keeps the caption attribution (source URL and license). There is no instagrapi and no browser automation.

`ai_disclosure: true` sends Instagram's `is_ai_generated=true` label. If the API rejects that field, the caption gains the line `AI disclosure: this video was made with AI tools.` Set `AI_DISCLOSURE=false` to turn that off.

Each live post is a row in the same SQLite file, table `publications`: source URLs, licenses, the fact report, and `published_at`. HTTP 429 and 5xx are retried with backoff. A 429 waits for `Retry-After` when it is a number of seconds. Instagram also stops at the Graph content-publishing limit (50 posts in 24 hours) and at a local count of the same window.

### Meta developer app

1. Create an app at [developers.facebook.com](https://developers.facebook.com/) and add the Instagram product (Instagram API with Facebook Login).
2. Link the professional Instagram account to a Facebook Page. The Page and the Instagram account have to be connected before Graph will publish.
3. Create a User token that can publish: `instagram_basic`, `instagram_content_publish`, `pages_show_list`, and `pages_read_engagement`. Copy the Instagram user id into `IG_USER_ID`.
4. Put `IG_APP_ID`, `IG_APP_SECRET`, and the short-lived `IG_ACCESS_TOKEN` in `.env`. `make publish` reads that file and does not override variables already in the environment. Do not commit `.env`.
5. Exchange the short-lived token for a long-lived one (about 60 days). The helper writes `edu/out/instagram_token.json`, which is gitignored. Copy the new token back into `IG_ACCESS_TOKEN`. Set `IG_TOKEN_EXPIRES_AT` to the expiry. A later `--live` run refreshes the token when it expires within 7 days.

```bash
make publish ARGS="--exchange-instagram"
```

Preview, and the real run. The first command never uploads. The second posts only the platforms whose credentials exist. A missing platform prints `No upload.`

```bash
make publish ARGS="--dry-run"
make publish
```

### YouTube desktop OAuth

Create an OAuth desktop client in Google Cloud, enable YouTube Data API v3, and put the client id and secret in `.env`. This command prints the consent URL. It does not open a browser. Paste the code from the redirect:

```bash
make publish ARGS="--auth-youtube"
make publish ARGS="--youtube-code 'THE_CODE'"
```

The refresh token is stored in `edu/out/youtube_token.json`.

### MacBook schedule

launchd, once a day at 09:00. Until `.env` has tokens, this stays a dry-run and does not upload. Save as `~/Library/LaunchAgents/com.reelsknowledge.publish.plist` and `launchctl load` it. Replace the repo path.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.reelsknowledge.publish</string>
  <key>WorkingDirectory</key>
  <string>/Users/you/reels_knowledege</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/make</string>
    <string>publish</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>9</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
</dict>
</plist>
```

cron, same idea. `make publish` posts once the tokens are in `.env`. Use `ARGS="--dry-run"` to keep the preview:

```cron
0 9 * * * cd /Users/you/reels_knowledege && /usr/bin/make publish
```

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
