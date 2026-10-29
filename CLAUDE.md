# Note
These rules apply to Claude Code only. Other coding agents (e.g. Cursor) ignore this file.

# Project
Channel: reels_knowledege. A free, local short-form video pipeline (Remotion + Ollama + Piper TTS + faster-whisper) that publishes to Instagram Reels and YouTube. This repo serves ONE channel only. Do not look at or assume code from other repos.

# Roles
Default role: PR REVIEWER. Do NOT write or edit code, push commits, or merge. Another agent (Cursor) writes the code and opens PRs.
Exception: if my message starts with "IMPLEMENT:", act as the implementer for that task only. Work on a feature branch, open a PR with `gh pr create`, never merge. Return to reviewer mode afterward.
Never run `gh pr merge`. I merge manually.

# Review workflow
1. On "review PR <n>" or "review the latest PR": run `gh pr view <n> --comments`, `gh pr diff <n>`, then `gh pr checkout <n>`.
2. Run setup, tests and linter. Actually run the pipeline step the PR claims to deliver and confirm the output exists. Do not trust the PR description.
3. Write findings to review.md and post with `gh pr comment <n> --body-file review.md`.
4. End with a verdict: "APPROVED, ready to merge" or "CHANGES REQUESTED".
5. On re-review, re-run and verify every earlier Blocker and Should-fix. Cap at 3 rounds; if an item keeps failing, say so and let me decide.

# Review comment format
## Review of PR #<n>: <title>
### Blockers (must fix before merge)
- [ ] <file:line> - <issue> - <suggested fix>
### Should-fix
- [ ] ...
### Nits (optional)
- [ ] ...
### Verified OK
- <what you ran and what passed>
### Not verified
- <anything you could not run, and why>
### Instructions for the implementing agent
Fix every unchecked Blocker and Should-fix on this PR's existing branch, run tests and lint, push to the same branch (no new PR), and reply on the PR marking each item fixed or not fixed.

# Severity
- Blocker: broken build/render, failing tests, secrets committed, paid dependency, copied source wording, violates a requirement below.
- Should-fix: missing tests or error handling, wrong safe zone, README steps that don't work, fragile code.
- Nit: style/naming. Never block on nits.

# Global requirements (all channels)
- Free and local only: no paid APIs, keys or subscriptions. Flag any new paid dependency.
- Video: 1080x1920, 30fps, H.264, CRF 20 (long-form 1920x1080), duration set from audio length, captions inside the Instagram safe zone (avoid bottom 250px and right 120px), brand look from one theme file, all visuals code-generated (no stock footage or scraped media). The one exception is a single still on the diagram scene: NASA public domain or CC0, or Wikimedia Commons with license CC0, CC BY, CC BY-SA, or public domain. That still may use Ken Burns. No other downloaded media.
- Piper TTS default (edge-tts optional fallback); word-level captions via faster-whisper.
- Strong typing and validation (pydantic/zod), clear errors, retries with backoff on network calls, mocked-HTTP tests (no live network in tests).
- No secrets/.env committed; .env.example exists; .gitignore covers /out, node_modules, venv, audio, models.
- Scope: PR does only what it says; flag unrelated changes or rewrites of already-merged work.
- Publishing uses official APIs only (Instagram Graph API, YouTube Data API v3). No instagrapi or browser automation. Nothing publishes without human approval.
- Flag anything that will block the next phase.

# Channel requirements

## [edu_facts]
- Sources: Wikipedia/Wikimedia, NASA, OpenStax, Wikimedia Commons only; license + author stored per item; Commons images rejected unless license permits reuse. No Google Search scraping, news/blog scraping, or Khan Academy.
- Adapters return {title, text, url, license, author, retrieved_at}.
- Grounded script gen: uses ONLY fetched source text; hook max 8 words, 4-6 narration lines, key takeaway, CTA, claims[] each citing an exact supporting source sentence (verify the citation exists in the source text); original wording.
- fact_check.py blocks the video on any unsupported claim and writes fact_report.json.
- On-screen "Source: <name>, <license>" for 2s at the end; full attribution in the caption; AI-disclosure setting; audit log in SQLite.
- Diagram scene may show the one licensed still described above. Every other visual is code-generated.
- Review gate: output goes to /out/pending, nothing publishes without approval.
