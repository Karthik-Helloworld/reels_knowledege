import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx

from edu.publish.envfile import load_env_file
from edu.publish.hosting import sigv4_headers
from edu.publish.http import request_with_backoff
from edu.publish.publish_instagram import (
    AI_CAPTION_LINE,
    exchange_long_lived_token,
    refresh_before_expiry,
)
from edu.publish.publish_youtube import (
    authorization_url,
    shorts_title,
    upload_short,
    youtube_description,
)
from edu.publish.records import ensure_log
from edu.publish.run import main as publish_main
from edu.publish.run import publish_due
from edu.scripts.queue import connect, isoformat
from edu.sources.http import client_headers

NOW = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)
SOURCE = "https://en.wikipedia.org/wiki/Example"
LICENSE = "CC BY-SA 4.0"

_KEYS = (
    "IG_ACCESS_TOKEN",
    "IG_USER_ID",
    "IG_APP_ID",
    "IG_APP_SECRET",
    "IG_TOKEN_EXPIRES_AT",
    "IG_GRAPH_VERSION",
    "IG_TOKEN_PATH",
    "YOUTUBE_CLIENT_ID",
    "YOUTUBE_CLIENT_SECRET",
    "YOUTUBE_REFRESH_TOKEN",
    "YOUTUBE_TOKEN_PATH",
    "VIDEO_HOST",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_BUCKET",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET",
    "R2_ENDPOINT",
    "R2_PUBLIC_BASE",
    "AI_DISCLOSURE",
    "REVIEW_CADENCE_PER_DAY",
)


def cleared(**overrides: str):
    values = {key: "" for key in _KEYS}
    values.update(overrides)
    return patch.dict(os.environ, values)


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), headers=client_headers())


def refuse(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"dry-run called {request.method} {request.url}")


class PublishTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "queue.sqlite"
        self.niche = self.root / "niche.yaml"
        self.niche.write_text("ai_disclosure: true\ncadence_per_day: 1\n", encoding="utf-8")
        self.connection = connect(self.db)
        ensure_log(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.tmp.cleanup()

    def add_item(self, item_id: str, topic: str, when: datetime, *, passed: bool = True) -> None:
        folder = self.root / item_id
        folder.mkdir()
        script = {
            "topic": topic,
            "claims": [{"text": topic, "source_url": SOURCE, "source_sentence": topic}],
        }
        (folder / "script.json").write_text(json.dumps(script), encoding="utf-8")
        (folder / "claims.json").write_text(json.dumps(script["claims"]), encoding="utf-8")
        report = {"passed": passed, "claims": [{"id": "hook", "passed": passed}]}
        (folder / "fact_report.json").write_text(json.dumps(report), encoding="utf-8")
        caption = "\n".join([topic, "", "#ScienceFacts", ""])
        (folder / "caption.txt").write_text(caption, encoding="utf-8")
        sources = [
            {
                "title": "Example",
                "url": SOURCE,
                "license": LICENSE,
                "author": "Wikipedia contributors",
            }
        ]
        (folder / "sources.json").write_text(json.dumps(sources), encoding="utf-8")
        (folder / "video.mp4").write_bytes(b"fake-mp4-bytes-for-upload")
        self.connection.execute(
            """
            INSERT INTO queue (
                id, topic, video_path, script_path, claims_path, fact_report_path,
                caption_path, status, scheduled_at, approved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'approved', ?, ?)
            """,
            (
                item_id,
                topic,
                str(folder / "video.mp4"),
                str(folder / "script.json"),
                str(folder / "claims.json"),
                str(folder / "fact_report.json"),
                str(folder / "caption.txt"),
                isoformat(when),
                isoformat(when),
            ),
        )
        self.connection.commit()

    def publish(
        self,
        handler,
        *,
        live: bool,
        per_day: float = 1,
        item_id: str | None = None,
    ) -> int:
        with client_for(handler) as client:
            return publish_due(
                database=self.db,
                now=NOW,
                live=live,
                per_day=per_day,
                niche_file=self.niche,
                client=client,
                sleep=lambda _delay: None,
                item_id=item_id,
            )

    def published(self, platform: str) -> list:
        return list(
            self.connection.execute(
                "SELECT * FROM publications WHERE platform = ? AND status = 'published'",
                (platform,),
            )
        )

    def test_dry_run_does_not_upload_or_log_a_post(self) -> None:
        self.add_item("black-hole", "Black hole", NOW)
        with cleared(
            IG_ACCESS_TOKEN="token",
            IG_USER_ID="1789",
            VIDEO_HOST="supabase",
            SUPABASE_URL="https://demo.supabase.co",
            SUPABASE_SERVICE_ROLE_KEY="service",
            SUPABASE_BUCKET="reels",
        ):
            code = self.publish(refuse, live=False)
        self.assertEqual(code, 0)
        self.assertEqual(self.published("instagram"), [])
        self.assertEqual(self.published("youtube"), [])

    def test_live_without_credentials_does_not_upload(self) -> None:
        self.add_item("black-hole", "Black hole", NOW)
        with cleared():
            code = self.publish(refuse, live=True)
        self.assertEqual(code, 0)
        self.assertEqual(self.published("instagram"), [])

    def test_future_video_is_not_due(self) -> None:
        self.add_item("mars", "Mars", NOW + timedelta(days=1))
        with cleared():
            code = self.publish(refuse, live=False)
        self.assertEqual(code, 0)

    def test_live_instagram_uploads_publishes_logs_and_deletes(self) -> None:
        self.add_item("black-hole", "Black hole", NOW)
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            path = request.url.path
            if path.endswith("/content_publishing_limit"):
                return httpx.Response(
                    200,
                    json={"data": [{"quota_usage": 1, "config": {"quota_total": 50}}]},
                )
            if path.endswith("/media") and request.method == "POST":
                form = parse_qs(request.content.decode())
                self.assertEqual(form["media_type"], ["REELS"])
                self.assertEqual(form["is_ai_generated"], ["true"])
                self.assertNotIn(LICENSE, form["caption"][0])
                self.assertTrue(form["video_url"][0].startswith("https://demo.supabase.co/"))
                return httpx.Response(200, json={"id": "container-1"})
            if path.endswith("/container-1"):
                return httpx.Response(200, json={"status_code": "FINISHED"})
            if path.endswith("/media_publish"):
                return httpx.Response(200, json={"id": "media-9"})
            if request.method == "POST" and "/storage/v1/object/" in path:
                self.assertEqual(request.headers["authorization"], "Bearer service")
                self.assertEqual(request.content, b"fake-mp4-bytes-for-upload")
                return httpx.Response(200, json={"Key": "reels/black-hole.mp4"})
            if request.method == "DELETE" and path.endswith("/storage/v1/object/reels"):
                return httpx.Response(200, json={"message": "deleted"})
            return httpx.Response(404, json={"error": path})

        with cleared(**_supabase()):
            code = self.publish(handler, live=True)
        self.assertEqual(code, 0)
        methods = [(item.method, item.url.path) for item in seen]
        self.assertIn(("POST", "/storage/v1/object/reels/black-hole.mp4"), methods)
        self.assertIn(("DELETE", "/storage/v1/object/reels"), methods)
        rows = self.published("instagram")
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0]["source_urls"]), [SOURCE])
        self.assertEqual(json.loads(rows[0]["licenses"]), [LICENSE])
        self.assertIn('"passed": true', rows[0]["fact_report"])
        self.assertEqual(rows[0]["published_at"], isoformat(NOW))
        self.assertEqual(rows[0]["remote_id"], "media-9")

    def test_ai_field_rejection_adds_a_caption_line(self) -> None:
        self.add_item("black-hole", "Black hole", NOW)
        posts: list[dict[str, list[str]]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/content_publishing_limit"):
                return httpx.Response(
                    200,
                    json={"data": [{"quota_usage": 0, "config": {"quota_total": 50}}]},
                )
            if path.endswith("/media") and request.method == "POST":
                form = parse_qs(request.content.decode())
                posts.append(form)
                if "is_ai_generated" in form:
                    return httpx.Response(
                        400,
                        json={"error": {"message": "is_ai_generated is unsupported"}},
                    )
                return httpx.Response(200, json={"id": "container-2"})
            if path.endswith("/container-2"):
                return httpx.Response(200, json={"status_code": "FINISHED"})
            if path.endswith("/media_publish"):
                return httpx.Response(200, json={"id": "media-2"})
            if "/storage/v1/object/" in path and request.method == "POST":
                return httpx.Response(200, json={"Key": "ok"})
            if request.method == "DELETE":
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(404, text=path)

        with cleared(**_supabase()):
            code = self.publish(handler, live=True)
        self.assertEqual(code, 0)
        self.assertIn("is_ai_generated", posts[0])
        self.assertNotIn("is_ai_generated", posts[1])
        self.assertIn(AI_CAPTION_LINE, posts[1]["caption"][0])

    def test_quota_blocks_the_upload(self) -> None:
        self.add_item("black-hole", "Black hole", NOW)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/content_publishing_limit"):
                return httpx.Response(
                    200,
                    json={"data": [{"quota_usage": 50, "config": {"quota_total": 50}}]},
                )
            raise AssertionError(request.url)

        with cleared(**_supabase()):
            code = self.publish(handler, live=True)
        self.assertEqual(code, 1)
        self.assertEqual(self.published("instagram"), [])

    def test_server_error_is_retried_on_the_container(self) -> None:
        self.add_item("black-hole", "Black hole", NOW)
        calls = {"media": 0}
        slept: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/content_publishing_limit"):
                return httpx.Response(
                    200,
                    json={"data": [{"quota_usage": 0, "config": {"quota_total": 50}}]},
                )
            if path.endswith("/media") and request.method == "POST":
                calls["media"] += 1
                if calls["media"] == 1:
                    return httpx.Response(502, text="down")
                return httpx.Response(200, json={"id": "container-3"})
            if path.endswith("/container-3"):
                return httpx.Response(200, json={"status_code": "FINISHED"})
            if path.endswith("/media_publish"):
                return httpx.Response(200, json={"id": "media-3"})
            if request.method == "POST":
                return httpx.Response(200, json={"Key": "ok"})
            if request.method == "DELETE":
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(404, text=path)

        with cleared(**_supabase()), patch("edu.publish.http.time.sleep", side_effect=slept.append):
            code = self.publish(handler, live=True)
        self.assertEqual(code, 0)
        self.assertEqual(calls["media"], 2)
        self.assertEqual(slept, [0.25])

    def test_cadence_publishes_one_due_video_per_run(self) -> None:
        self.add_item("black-hole", "Black hole", NOW)
        self.add_item("mars", "Mars", NOW)
        published_ids: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/content_publishing_limit"):
                return httpx.Response(
                    200,
                    json={"data": [{"quota_usage": 0, "config": {"quota_total": 50}}]},
                )
            if path.endswith("/media") and request.method == "POST":
                form = parse_qs(request.content.decode())
                published_ids.append(form["video_url"][0])
                return httpx.Response(200, json={"id": "container"})
            if path.endswith("/container"):
                return httpx.Response(200, json={"status_code": "FINISHED"})
            if path.endswith("/media_publish"):
                return httpx.Response(200, json={"id": "media"})
            if request.method == "POST":
                return httpx.Response(200, json={"Key": "ok"})
            if request.method == "DELETE":
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(404, text=path)

        with cleared(**_supabase()):
            code = self.publish(handler, live=True, per_day=1)
        self.assertEqual(code, 0)
        self.assertEqual(len(published_ids), 1)
        self.assertIn("black-hole.mp4", published_ids[0])
        self.assertEqual(len(self.published("instagram")), 1)

    def test_failed_fact_report_is_not_published(self) -> None:
        self.add_item("black-hole", "Black hole", NOW, passed=False)
        with cleared(**_supabase()):
            code = self.publish(refuse, live=True)
        self.assertEqual(code, 1)
        self.assertEqual(self.published("instagram"), [])

    def test_token_exchange_and_refresh_window(self) -> None:
        tokens: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.params["grant_type"], "fb_exchange_token")
            tokens.append(request.url.params["fb_exchange_token"])
            return httpx.Response(200, json={"access_token": "long", "expires_in": 5184000})

        with client_for(handler) as client:
            payload = exchange_long_lived_token(
                client,
                app_id="app",
                app_secret="secret",
                token="short",
            )
            fresh = refresh_before_expiry(
                client,
                app_id="app",
                app_secret="secret",
                token="long",
                expires_at=NOW + timedelta(days=30),
                now=NOW,
            )
        self.assertEqual(payload["access_token"], "long")
        self.assertEqual(tokens, ["short"])
        self.assertIsNone(fresh)
        with client_for(handler) as client:
            soon = refresh_before_expiry(
                client,
                app_id="app",
                app_secret="secret",
                token="long",
                expires_at=NOW + timedelta(days=2),
                now=NOW,
            )
        self.assertEqual(soon["access_token"], "long")
        self.assertEqual(tokens, ["short", "long"])

    def test_youtube_title_description_and_resumable_upload(self) -> None:
        title = shorts_title("Black hole")
        self.assertLessEqual(len(title), 100)
        self.assertTrue(title.endswith("#Shorts"))
        long_title = shorts_title("x" * 200)
        self.assertLessEqual(len(long_title), 100)
        self.assertTrue(long_title.endswith("#Shorts"))
        description = youtube_description(
            "Black hole\n\n" + SOURCE + "\n" + LICENSE,
            [SOURCE],
            [LICENSE],
        )
        self.assertIn(SOURCE, description)
        self.assertIn(LICENSE, description)
        url = authorization_url("client-id")
        self.assertIn("response_type=code", url)
        self.assertIn("youtube.upload", url)
        puts: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                body = json.loads(request.content.decode())
                self.assertLessEqual(len(body["snippet"]["title"]), 100)
                self.assertIn("#Shorts", body["snippet"]["title"])
                self.assertIn(SOURCE, body["snippet"]["description"])
                self.assertEqual(request.headers["x-upload-content-type"], "video/mp4")
                return httpx.Response(200, headers={"location": "https://upload.example/session"})
            puts.append(request)
            if len(puts) == 1:
                return httpx.Response(308, headers={"range": "bytes=0-4"})
            self.assertIn("Content-Range", request.headers)
            return httpx.Response(200, json={"id": "yt-1"})

        with client_for(handler) as client:
            video_id = upload_short(
                client,
                access_token="access",
                title=title,
                description=description,
                payload=b"0123456789",
            )
        self.assertEqual(video_id, "yt-1")
        self.assertEqual(len(puts), 2)

    def test_r2_upload_is_signed_and_not_used_on_dry_run(self) -> None:
        headers = sigv4_headers(
            method="PUT",
            url="https://acct.r2.cloudflarestorage.com/reels/black-hole.mp4",
            payload=b"fake-mp4-bytes-for-upload",
            access_key="AKID",
            secret_key="secret",
            now=NOW,
            content_type="video/mp4",
        )
        self.assertTrue(headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKID/"))
        self.assertIn("/auto/s3/aws4_request", headers["Authorization"])
        self.assertEqual(headers["x-amz-date"], "20261028T150000Z")
        self.add_item("black-hole", "Black hole", NOW)
        with cleared(
            IG_ACCESS_TOKEN="token",
            IG_USER_ID="1789",
            VIDEO_HOST="r2",
            R2_ACCESS_KEY_ID="AKID",
            R2_SECRET_ACCESS_KEY="secret",
            R2_BUCKET="reels",
            R2_ENDPOINT="https://acct.r2.cloudflarestorage.com",
            R2_PUBLIC_BASE="https://media.example",
        ):
            code = self.publish(refuse, live=False)
        self.assertEqual(code, 0)

    def test_live_youtube_refreshes_and_uploads_the_local_file(self) -> None:
        self.add_item("black-hole", "Black hole", NOW)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "oauth2.googleapis.com":
                form = parse_qs(request.content.decode())
                self.assertEqual(form["grant_type"], ["refresh_token"])
                return httpx.Response(200, json={"access_token": "access"})
            if request.method == "POST":
                return httpx.Response(200, headers={"location": "https://upload.example/session"})
            self.assertEqual(request.content, b"fake-mp4-bytes-for-upload")
            return httpx.Response(200, json={"id": "yt-9"})

        with cleared(
            YOUTUBE_CLIENT_ID="client",
            YOUTUBE_CLIENT_SECRET="secret",
            YOUTUBE_REFRESH_TOKEN="refresh",
        ):
            code = self.publish(handler, live=True)
        self.assertEqual(code, 0)
        rows = self.published("youtube")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["remote_id"], "yt-9")
        self.assertEqual(json.loads(rows[0]["source_urls"]), [SOURCE])
        self.assertEqual(json.loads(rows[0]["licenses"]), [LICENSE])
        self.assertEqual(self.published("instagram"), [])

    def test_main_without_live_does_not_post(self) -> None:
        self.add_item("black-hole", "Black hole", NOW)
        env = self.root / "blank.env"
        env.write_text("\n", encoding="utf-8")
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            raise AssertionError(request.url)

        with cleared(**_supabase()), patch(
            "edu.publish.run.make_client",
            return_value=client_for(handler),
        ):
            code = publish_main(
                [
                    "--env",
                    str(env),
                    "--db",
                    str(self.db),
                    "--now",
                    isoformat(NOW),
                    "--cadence-per-day",
                    "1",
                    "--niche",
                    str(self.niche),
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(seen, [])
        self.assertEqual(self.published("instagram"), [])

    def test_main_live_flag_reaches_instagram(self) -> None:
        self.add_item("black-hole", "Black hole", NOW)
        env = self.root / "blank.env"
        env.write_text("\n", encoding="utf-8")
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(404, text="stop")

        with cleared(**_supabase()), patch(
            "edu.publish.run.make_client",
            return_value=client_for(handler),
        ):
            code = publish_main(
                [
                    "--live",
                    "--env",
                    str(env),
                    "--db",
                    str(self.db),
                    "--now",
                    isoformat(NOW),
                    "--cadence-per-day",
                    "1",
                    "--niche",
                    str(self.niche),
                ]
            )
        self.assertEqual(code, 1)
        self.assertTrue(any(path.endswith("/content_publishing_limit") for path in seen))

    def test_main_live_and_dry_run_does_not_post(self) -> None:
        self.add_item("black-hole", "Black hole", NOW)
        env = self.root / "blank.env"
        env.write_text("\n", encoding="utf-8")

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError(request.url)

        with cleared(**_supabase()), patch(
            "edu.publish.run.make_client",
            return_value=client_for(handler),
        ):
            code = publish_main(
                [
                    "--live",
                    "--dry-run",
                    "--env",
                    str(env),
                    "--db",
                    str(self.db),
                    "--now",
                    isoformat(NOW),
                    "--cadence-per-day",
                    "1",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(self.published("instagram"), [])

    def test_refreshed_token_file_overrides_the_environment(self) -> None:
        self.add_item("black-hole", "Black hole", NOW)
        token_path = self.root / "instagram_token.json"
        token_path.write_text(
            json.dumps({"access_token": "from-file", "refreshed_at": isoformat(NOW)}),
            encoding="utf-8",
        )
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/content_publishing_limit"):
                seen.append(request.url.params.get("access_token", ""))
                return httpx.Response(
                    200,
                    json={"data": [{"quota_usage": 50, "config": {"quota_total": 50}}]},
                )
            raise AssertionError(request.url)

        values = _supabase()
        values["IG_ACCESS_TOKEN"] = "from-env"
        values["IG_TOKEN_PATH"] = str(token_path)
        with cleared(**values):
            code = self.publish(handler, live=True)
        self.assertEqual(code, 1)
        self.assertEqual(seen, ["from-file"])

    def test_a_source_without_a_license_is_not_guessed(self) -> None:
        self.add_item("black-hole", "Black hole", NOW)
        folder = self.root / "black-hole"
        (folder / "sources.json").write_text(
            json.dumps([{"url": SOURCE}]),
            encoding="utf-8",
        )
        (folder / "caption.txt").write_text(
            f"Black hole\n\n{SOURCE}\n{LICENSE}\n",
            encoding="utf-8",
        )
        with cleared(**_supabase()):
            code = self.publish(refuse, live=True)
        self.assertEqual(code, 1)
        self.assertEqual(self.published("instagram"), [])

    def test_env_file_does_not_override_the_process(self) -> None:
        path = self.root / ".env"
        path.write_text("IG_USER_ID=from-file\nIG_ACCESS_TOKEN=from-file\n", encoding="utf-8")
        with patch.dict(os.environ, {"IG_USER_ID": "already"}, clear=False):
            os.environ.pop("IG_ACCESS_TOKEN", None)
            filled = load_env_file(path)
            self.assertEqual(os.environ["IG_USER_ID"], "already")
            self.assertEqual(os.environ["IG_ACCESS_TOKEN"], "from-file")
            self.assertEqual(filled, ["IG_ACCESS_TOKEN"])

    def test_retry_after_is_honored(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, headers={"retry-after": "3"}, text="slow")
            return httpx.Response(200, json={"ok": True})

        slept: list[float] = []
        with client_for(handler) as client:
            response = request_with_backoff(
                client,
                "GET",
                "https://graph.facebook.com/v21.0/limit",
                sleep=slept.append,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(slept, [3.0])


def _supabase() -> dict[str, str]:
    return {
        "IG_ACCESS_TOKEN": "token",
        "IG_USER_ID": "1789",
        "VIDEO_HOST": "supabase",
        "SUPABASE_URL": "https://demo.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "service",
        "SUPABASE_BUCKET": "reels",
    }
