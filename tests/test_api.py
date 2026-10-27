import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient

import api
from video_tool.config import DEFAULT_FILE, load_config
from video_tool.pipeline import TaskCancelled, collect_or_run
from video_tool.models import Comment, CommentResult, Video
from video_tool.storage import Store


class ApiTests(unittest.TestCase):
    def setUp(self):
        api.CANCEL_EVENTS.clear()

    def tearDown(self):
        api.CANCEL_EVENTS.clear()

    def _root(self, directory: str) -> Path:
        root = Path(directory)
        (root / "config.yaml.bak").write_text(DEFAULT_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        return root

    def test_display_date_uses_shanghai_day(self):
        self.assertEqual(api._display_date('2026-10-27T20:04:23+00:00'), '2026-10-28')
        self.assertEqual(api._display_date('2026-10-27T12:04:23+00:00'), '2026-10-27')
        self.assertEqual(api._display_date('2026-10-28T01:00:00+08:00'), '2026-10-28')
        self.assertEqual(api._display_date(''), '')

    def test_pause_edit_resume_preserves_identity_and_saved_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            with patch.object(api, "ROOT", root), patch.object(api.threading, "Thread"):
                client = TestClient(api.app)
                original = client.post('/api/tasks', json={
                    'mode': 'collect', 'urls': ['https://www.douyin.com/video/123'],
                    'options': {'title': '原任务'},
                }).json()['data']
                task_id = original['id']
                path = f'/api/tasks/{task_id}'
                self.assertEqual(client.put(path, json={'mode': 'collect'}).status_code, 409)
                self.assertEqual(client.post(path + '/pause').status_code, 200)
                self.assertTrue(api.CANCEL_EVENTS[task_id].is_set())
                self.assertEqual(client.post(path + '/resume').status_code, 409)
                # Simulate a worker saving stale running/completed data after the pause request.
                original.update(state='done', progress=70)
                api._save_task(original)
                self.assertEqual(api._get_task(task_id)['state'], 'paused')
                api.CANCEL_EVENTS.clear()
                self.assertEqual(client.get('/api/tasks').json()['data']['items'][0]['state'], 'paused')
                edited = client.put(path, json={'mode': 'collect',
                    'urls': ['https://www.douyin.com/video/456'],
                    'options': {'title': '修改后的任务', 'include_comments': True, 'max_comments_per_video': 25, 'max_videos_per_collection': None}})
                self.assertEqual(edited.status_code, 200, edited.text)
                task = edited.json()['data']
                self.assertEqual(task['title'], '修改后的任务')
                saved = client.get(path).json()['data']
                self.assertEqual(saved['options']['max_comments_per_video'], 25)
                self.assertIsNone(saved['options']['max_videos_per_collection'])
                self.assertEqual(task['created_at'], original['created_at'])
                self.assertEqual(task['progress'], 70)
                resumed = client.post(path + '/resume')
                self.assertEqual(resumed.status_code, 202, resumed.text)
                task = resumed.json()['data']
                self.assertEqual(task['id'], task_id)
                self.assertEqual(task['urls'], ['https://www.douyin.com/video/456'])
                self.assertFalse(task['options']['force_reprocess'])
                self.assertEqual(task['options']['max_comments_per_video'], 25)
                self.assertEqual(client.post(path + '/resume').status_code, 409)

    def test_paused_queued_task_does_not_collect(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            with patch.object(api, 'ROOT', root), patch.object(api.threading, 'Thread'):
                client = TestClient(api.app)
                task = client.post('/api/tasks', json={'mode': 'collect', 'urls': ['https://www.douyin.com/video/123']}).json()['data']
                task_id = task['id']
                client.post(f'/api/tasks/{task_id}/pause')
                request = api.TaskRequest(mode=task['mode'], urls=task['urls'], options=task['options'])
                with patch.object(api, 'collect_or_run') as collect:
                    api._run_task(task_id, request, api._config())
                    collect.assert_not_called()
                self.assertEqual(api._get_task(task_id)['state'], 'paused')
                self.assertNotIn(task_id, api.CANCEL_EVENTS)

    def test_queued_tasks_do_not_execute_together(self):
        started = threading.Event()
        release = threading.Event()
        executed = []
        def work(task_id, request, config):
            executed.append(task_id)
            if task_id == "first":
                started.set()
                release.wait(3)
        with patch.object(api, "_run_task", side_effect=work):
            first = threading.Thread(target=api._run_queued_task, args=("first", None, {}))
            second = threading.Thread(target=api._run_queued_task, args=("second", None, {}))
            first.start()
            self.assertTrue(started.wait(2))
            try:
                second.start()
                self.assertEqual(executed, ["first"])
            finally:
                release.set()
                first.join(3)
                second.join(3)
            self.assertEqual(executed, ["first", "second"])

    def test_tasks_are_persisted_and_recovered_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            with patch.object(api, "ROOT", root), patch.object(api, "_run_task"):
                client = TestClient(api.app)
                created = client.post("/api/tasks", json={
                    "mode": "collect", "urls": ["分享文案 https://v.douyin.com/example/ 复制打开抖音"]
                })
                self.assertEqual(created.status_code, 202)
                task_id = created.json()["data"]["id"]
                self.assertEqual(created.json()["data"]["urls"], ["https://v.douyin.com/example/"])
                self.assertEqual(client.get("/api/tasks").json()["data"]["total"], 1)
                api.CANCEL_EVENTS.clear()
                recovered = client.get(f"/api/tasks/{task_id}").json()["data"]
                self.assertEqual(recovered["state"], "pending")
                listed = client.get("/api/tasks").json()["data"]["items"][0]
                self.assertEqual(listed["state"], "failed")
                self.assertIn("服务重启", listed["error"])
                self.assertEqual(client.delete(f"/api/tasks/{task_id}").status_code, 200)
                self.assertEqual(client.get("/api/tasks").json()["data"]["total"], 0)

    def test_retry_reuses_task_and_rejects_duplicate_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            with patch.object(api, "ROOT", root), patch.object(api.threading, "Thread"):
                client = TestClient(api.app)
                original = client.post("/api/tasks", json={
                    "mode": "collect", "urls": ["https://www.douyin.com/video/123"],
                    "options": {"title": "原任务"},
                }).json()["data"]
                task_id = original["id"]
                self.assertEqual(client.post(f"/api/tasks/{task_id}/retry").status_code, 409)
                api.CANCEL_EVENTS.clear()
                original.update(state="failed", progress=70, error="旧错误", finished_at="old")
                original["steps"]["collect"] = "failed"
                api._save_task(original)
                retried = client.post(f"/api/tasks/{task_id}/retry")
                self.assertEqual(retried.status_code, 202, retried.text)
                task = retried.json()["data"]
                self.assertEqual(task["id"], task_id)
                self.assertEqual(task["title"], original["title"])
                self.assertEqual(task["created_at"], original["created_at"])
                self.assertEqual(task["state"], "pending")
                self.assertEqual(task["progress"], 0)
                self.assertEqual(task["error"], "")
                self.assertIsNone(task["finished_at"])
                self.assertTrue(all(value == "pending" for value in task["steps"].values()))
                self.assertEqual(client.get("/api/tasks").json()["data"]["total"], 1)
                self.assertEqual(client.post(f"/api/tasks/{task_id}/retry").status_code, 409)

    def test_update_reuses_collection_task_without_force_reprocess(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            with patch.object(api, "ROOT", root), patch.object(api.threading, "Thread"):
                client = TestClient(api.app)
                task = client.post("/api/tasks", json={"mode": "collect",
                    "urls": ["https://www.douyin.com/collection/999"],
                    "options": {"force_reprocess": True}}).json()["data"]
                url = f"/api/tasks/{task['id']}/update"
                self.assertEqual(client.post(url).status_code, 409)
                api.CANCEL_EVENTS.clear()
                response = client.post(url)
                self.assertEqual(response.status_code, 202, response.text)
                updated = response.json()["data"]
                self.assertEqual(updated["id"], task["id"])
                self.assertTrue(updated["options"]["update_only"])
                self.assertFalse(updated["options"]["force_reprocess"])
                self.assertEqual(client.get("/api/tasks").json()["data"]["total"], 1)
                api.CANCEL_EVENTS.clear()
                retried = client.post(f"/api/tasks/{task['id']}/retry").json()["data"]
                self.assertNotIn("update_only", retried["options"])

    def test_worker_updates_persisted_steps_and_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            with patch.object(api, "ROOT", root), patch.object(api, "_run_task"):
                task = api._create_task(api.TaskRequest(
                    mode="run", urls=["https://www.douyin.com/video/123"],
                    options={"include_comments": True},
                ))

            def completed(_config, _urls, _full, status, **kwargs):
                kwargs["on_progress"]("collect", 25)
                kwargs["on_progress"]("comments", 45)
                kwargs["on_progress"]("download", 60)
                kwargs["on_progress"]("transcribe", 75)
                kwargs["on_progress"]("report", 92)
                status["failed"] = 0
                return []

            with patch.object(api, "ROOT", root), patch.object(api, "collect_or_run", side_effect=completed):
                api._run_task(task["id"], api.TaskRequest(
                    mode="run", urls=task["urls"], options={"include_comments": True}
                ), load_config(root / "config.yaml.bak"))
                saved = api._get_task(task["id"])
                self.assertEqual(saved["state"], "done")
                self.assertEqual(saved["progress"], 100)
                self.assertTrue(all(value == "done" for value in saved["steps"].values()))

    def test_tag_options_and_multi_tag_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            with patch.object(api, "ROOT", root):
                store = api._store()
                for identifier, author, desc in (("1", "甲", "#ai #知识库 #ai"), ("2", "乙", "#ai知识库"), ("3", "甲", "#生活")):
                    store.save_video(Video("douyin", identifier, f"https://www.douyin.com/video/{identifier}",
                                           author_name=author, description=desc))
                store.close()
                client = TestClient(api.app)
                options = client.get("/api/video-tags", params={"q": "#ai"}).json()["data"]["items"]
                self.assertEqual(options, [{"name": "ai", "count": 1}, {"name": "ai知识库", "count": 1}])
                result = client.get("/api/videos", params=[("tags", "ai"), ("tags", "生活")]).json()["data"]
                self.assertEqual(result["total"], 2)
                result = client.get("/api/videos", params=[("tags", "ai知识库"), ("author_names", "甲")]).json()["data"]
                self.assertEqual(result["total"], 0)
                self.assertEqual(client.get("/api/videos", params={"tags": "#知识库"}).json()["data"]["total"], 1)
                self.assertEqual(client.get("/api/videos").json()["data"]["total"], 3)

    def test_video_list_extracts_hashtags_from_description(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            with patch.object(api, "ROOT", root):
                store = api._store()
                store.save_video(Video("douyin", "123", "https://www.douyin.com/video/123",
                    description="简介 #知识库 #ai #ai知识库 #实用干货分享 #智能体 #ai", hashtags=["知识库"]))
                store.close()
                item = TestClient(api.app).get("/api/videos").json()["data"]["items"][0]
                self.assertEqual(item["hashtags"], ["知识库", "ai", "ai知识库", "实用干货分享", "智能体"])

    def test_comment_marks_persist_after_recrawl(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            with patch.object(api, "ROOT", root):
                store = api._store()
                result = CommentResult([Comment("1", "评论"), Comment("2", "回复", parent_id="1")], True, "到底")
                store.save_comments("douyin", "123", result)
                store.close()
                client = TestClient(api.app)
                base = "/api/videos/douyin/123/comments"
                self.assertEqual(client.put(base + "/2/mark", json={"marked": True}).status_code, 200)
                store = api._store()
                store.save_comments("douyin", "123", result, replace=True)
                store.close()
                items = client.get(base + "?marked_only=true").json()["data"]["items"]
                self.assertEqual([item["comment_id"] for item in items], ["2"])
                self.assertTrue(items[0]["marked"])
                self.assertEqual(client.put(base + "/missing/mark", json={"marked": True}).status_code, 404)
                self.assertEqual(client.put(base + "/2/mark", json={"marked": False}).status_code, 200)
                self.assertEqual(client.get(base + "?marked_only=true").json()["data"]["total"], 0)

    def test_author_name_options_and_multiple_name_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            with patch.object(api, "ROOT", root):
                store = api._store()
                for identifier, name in (("1", "小严在努力"), ("2", "小严在努力"), ("3", "小王"), ("4", "其他作者")):
                    store.save_video(Video("douyin", identifier, f"https://www.douyin.com/video/{identifier}",
                                           author_name=name, author_id=f"id-{identifier}"))
                store.close()
                client = TestClient(api.app)
                result = client.get("/api/video-authors", params={"q": "小严"}).json()["data"]["items"]
                self.assertEqual(result, [{"name": "小严在努力", "count": 2}])
                self.assertEqual(client.get("/api/video-authors", params={"q": "id-1"}).json()["data"]["items"], [])
                selected = client.get("/api/videos", params=[("author_names", "小严在努力"), ("author_names", "小王")]).json()["data"]
                self.assertEqual(selected["total"], 3)
                self.assertEqual({v["author_name"] for v in selected["items"]}, {"小严在努力", "小王"})
                self.assertEqual(client.get("/api/videos", params={"author_names": "小严"}).json()["data"]["total"], 0)
                self.assertEqual(client.get("/api/videos").json()["data"]["total"], 4)

    def test_author_file_migration_preserves_data_and_updates_playback_path(self):
        from video_tool.migration import organize_author_files
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            with patch.object(api, "ROOT", root):
                config = api._config()
                video = Video("douyin", "123", "https://www.douyin.com/video/123",
                              title="标题", author_name="作者/名字", comments_complete=True,
                              transcript="转写正文")
                media = Path(config["download"]["output_dir"]) / "标题_123.mp4"
                media.parent.mkdir(parents=True, exist_ok=True)
                media.write_bytes(b"video")
                video.local_media_path = str(media)
                old_report = Path(config["report"]["output_dir"]) / f"{api.video_stem(video)}.md"
                old_report.parent.mkdir(parents=True, exist_ok=True)
                old_report.write_text("完整报告", encoding="utf-8")
                store = api._store(config)
                store.save_video(video)
                store.close()
                self.assertEqual(len(organize_author_files(config)), 2)
                self.assertFalse(media.exists())
                self.assertFalse(old_report.exists())
                store = api._store(config)
                saved = store.get_video("douyin", "123")
                store.close()
                self.assertEqual(Path(saved.local_media_path).parent.name, "作者_名字")
                self.assertEqual(Path(saved.local_media_path).read_bytes(), b"video")
                self.assertEqual(saved.transcript, "转写正文")
                self.assertTrue(saved.comments_complete)
                self.assertEqual(api.report_path(config["report"]["output_dir"], saved).read_text(encoding="utf-8"), "完整报告")
                self.assertEqual(organize_author_files(config), [])
                client = TestClient(api.app)
                self.assertEqual(client.get("/api/videos/douyin/123/media").status_code, 200)
                reports = client.get("/api/reports").json()["data"]["items"]
                name = reports[0]["name"]
                self.assertIn("作者_名字/", name)
                self.assertEqual(client.get(f"/api/reports/{name}").status_code, 200)
                self.assertNotEqual(client.get("/api/reports/../config.yaml").status_code, 200)

    def test_report_button_reveals_existing_report_in_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            with patch.object(api, "ROOT", root), patch.object(api.subprocess, "Popen") as launch:
                config = api._config()
                store = api._store(config)
                video = Video("douyin", "123", "https://www.douyin.com/video/123", title="报告 测试")
                store.save_video(video)
                store.close()
                client = TestClient(api.app)
                url = "/api/videos/douyin/123/report/reveal"
                self.assertEqual(client.post(url).status_code, 404)
                launch.assert_not_called()
                folder = Path(config["report"]["output_dir"])
                folder.mkdir(parents=True, exist_ok=True)
                report = api.report_path(folder, video)
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text("报告", encoding="utf-8")
                response = client.post(url)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["data"]["path"], str(report.resolve()))
                launch.assert_called_once_with(f'explorer.exe /select,"{report.resolve()}"')

    def test_downloaded_video_supports_playback_and_seeking(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            media = root / "video.mp4"
            media.write_bytes(b"0123456789")
            with patch.object(api, "ROOT", root):
                store = api._store()
                store.save_video(Video("douyin", "123", "https://www.douyin.com/video/123",
                                       local_media_path=str(media)))
                store.close()
                client = TestClient(api.app)
                url = "/api/videos/douyin/123/media"
                response = client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"], "video/mp4")
                self.assertEqual(response.content, b"0123456789")
                partial = client.get(url, headers={"Range": "bytes=2-5"})
                self.assertEqual(partial.status_code, 206)
                self.assertEqual(partial.content, b"2345")
                self.assertEqual(partial.headers["content-range"], "bytes 2-5/10")
                media.unlink()
                self.assertEqual(client.get(url).status_code, 404)
                self.assertEqual(client.get("/api/videos/douyin/missing/media").status_code, 404)

    def test_refresh_video_updates_metrics_and_comments_preserves_assets_and_marks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            with patch.object(api, "ROOT", root):
                config = api._config()
                store = api._store(config)
                old = Video("douyin", "123", "https://www.douyin.com/video/123", title="原标题",
                            local_media_path=str(root / "video.mp4"), transcript_status="complete",
                            transcript="保留转写", likes=1)
                store.save_video(old)
                store.save_comments("douyin", "123", CommentResult([Comment("1", "旧评论")], True, "到底"))
                store.mark_comment("douyin", "123", "1", True)
                store.close()
                fresh = Video("douyin", "123", old.url, metadata_status="complete", likes=20, favorites=10,
                              shares=5, description="最新简介 #ai")
                with patch.object(api, "DouyinAdapter") as adapter:
                    adapter.return_value.read_video.return_value = fresh
                    adapter.return_value.read_comments.return_value = CommentResult(
                        [Comment("1", "旧评论", likes=8), Comment("2", "新评论")], True, "到底")
                    api._execute_video_action(config, "123", "refresh")
                store = api._store(config)
                saved = store.get_video("douyin", "123")
                self.assertEqual((saved.likes, saved.favorites, saved.shares), (20, 10, 5))
                self.assertEqual(saved.local_media_path, old.local_media_path)
                self.assertEqual(saved.transcript, "保留转写")
                self.assertEqual(saved.transcript_status, "complete")
                self.assertEqual(saved.title, "原标题")
                self.assertEqual(len(store.comments("douyin", "123")), 2)
                self.assertEqual(store.marked_comments("douyin", "123"), {"1"})
                store.close()
                adapter.return_value.close.assert_called_once()

    def test_video_status_and_individual_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            media = root / "video.mp4"
            media.write_bytes(b"video")
            with patch.object(api, "ROOT", root), patch.object(api.threading, "Thread"):
                store = api._store()
                video = Video("douyin", "123", "https://www.douyin.com/video/123",
                              title="测试视频", metadata_status="complete", comments_complete=True,
                              local_media_path=str(media), transcript_status="failed")
                store.save_video(video)
                store.close()
                client = TestClient(api.app)
                listing = client.get("/api/videos").json()["data"]["items"][0]
                self.assertTrue(listing["download_complete"])
                self.assertTrue(listing["comments_complete"])
                self.assertFalse(listing["report_complete"])
                for action in ("download", "comments", "transcribe", "report", "refresh"):
                    api.CANCEL_EVENTS.clear()
                    response = client.post(f"/api/videos/douyin/123/actions/{action}")
                    self.assertEqual(response.status_code, 202, response.text)
                    task = response.json()["data"]
                    self.assertEqual(task["options"]["video_action"], action)
                    listing = client.get("/api/videos").json()["data"]["items"][0]
                    self.assertIn(action, listing["running_actions"])
                    duplicate = client.post(f"/api/videos/douyin/123/actions/{action}")
                    self.assertEqual(duplicate.status_code, 409)
                    self.assertEqual(client.delete("/api/videos/douyin/123").status_code, 409)
                    with patch.object(api, "_execute_video_action") as execute:
                        api._run_task(task["id"], api.TaskRequest(mode=task["mode"], urls=task["urls"],
                                      options=task["options"]), api._config())
                    execute.assert_called_once_with(unittest.mock.ANY, "123", action)
                    self.assertEqual(api._get_task(task["id"])["state"], "done")
                media.unlink()
                listing = client.get("/api/videos").json()["data"]["items"][0]
                self.assertFalse(listing["download_complete"])
                self.assertEqual(client.post("/api/videos/douyin/123/actions/transcribe").status_code, 422)

    def test_worker_persists_full_failure_details(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            with patch.object(api, "ROOT", root), patch.object(api.threading, "Thread"):
                task = api._create_task(api.TaskRequest(
                    mode="run", urls=["https://www.douyin.com/video/123"]))
                errors = ["找不到 FFmpeg：ffmpeg", "转写请求失败：" + "详细原因" * 100]
                def failed(_config, _urls, _full, status, **kwargs):
                    status.update(failed=1, failed_steps=["transcribe"], errors=errors)
                    return []
                with patch.object(api, "collect_or_run", side_effect=failed):
                    api._run_task(task["id"], api.TaskRequest(mode="run", urls=task["urls"]),
                                  load_config(root / "config.yaml.bak"))
                saved = api._get_task(task["id"])
                self.assertEqual(saved["error"], "\n\n".join(errors))
                self.assertEqual(saved["state"], "failed")
                self.assertEqual(saved["steps"]["transcribe"], "failed")

    def test_worker_respects_individual_processing_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            options = {
                "title": "只下载",
                "download_video": True,
                "transcribe_text": False,
                "include_comments": False,
                "generate_report": False,
            }
            with patch.object(api, "ROOT", root), patch.object(api, "_run_task"):
                task = api._create_task(api.TaskRequest(
                    mode="run", urls=["https://www.douyin.com/video/123"], options=options,
                ))

            def completed(_config, _urls, full, status, **kwargs):
                self.assertTrue(full)
                self.assertFalse(kwargs["include_comments"])
                self.assertFalse(kwargs["transcribe_media"])
                self.assertFalse(kwargs["generate_reports"])
                kwargs["on_progress"]("collect", 25)
                kwargs["on_progress"]("download", 60)
                status["failed"] = 0
                return []

            with patch.object(api, "ROOT", root), patch.object(api, "collect_or_run", side_effect=completed):
                api._run_task(task["id"], api.TaskRequest(
                    mode="run", urls=task["urls"], options=options,
                ), load_config(root / "config.yaml.bak"))
                saved = api._get_task(task["id"])
                self.assertEqual(saved["steps"]["collect"], "done")
                self.assertEqual(saved["steps"]["download"], "done")
                self.assertEqual(saved["steps"]["comments"], "pending")
                self.assertEqual(saved["steps"]["transcribe"], "pending")
                self.assertEqual(saved["steps"]["report"], "pending")

    def test_config_validation_and_secret_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            with patch.object(api, "ROOT", root):
                client = TestClient(api.app)
                saved = client.put("/api/config", json={"values": {
                    "crawl.max_retries": "4", "crawl.delay_seconds": "0.5",
                    "asr.api_key": "secret-key"
                }})
                self.assertEqual(saved.status_code, 200, saved.text)
                response = client.get("/api/config").json()["data"]
                self.assertEqual(response["values"]["crawl.max_retries"], 4)
                self.assertEqual(response["values"]["crawl.delay_seconds"], 0.5)
                self.assertNotIn("asr.api_key", response["values"])
                self.assertTrue(response["secrets"]["asr.api_key"])
                revealed = client.get("/api/config/api-key")
                self.assertEqual(revealed.status_code, 200)
                self.assertEqual(revealed.json()["data"]["value"], "secret-key")
                self.assertEqual(revealed.headers["cache-control"], "no-store")
                preserved = client.put("/api/config", json={"values": {"asr.api_key": ""}})
                self.assertEqual(preserved.status_code, 200, preserved.text)
                raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
                self.assertEqual(raw["asr"]["api_key"], "secret-key")
                rejected = client.put("/api/config", json={"values": {"unknown.option": 1}})
                self.assertEqual(rejected.status_code, 422)

    def test_config_nullable_limits_can_be_saved_and_round_tripped(self):
        keys = (
            "inputs.max_videos_per_profile", "inputs.max_videos_per_collection",
            "crawl.max_comments_per_video", "report.top_comments",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            with patch.object(api, "ROOT", root):
                client = TestClient(api.app)
                for value, expected in ((None, None), ("", None), ("12", 12)):
                    with self.subTest(value=value):
                        saved = client.put("/api/config", json={
                            "values": dict.fromkeys(keys, value),
                        })
                        self.assertEqual(saved.status_code, 200, saved.text)
                        values = client.get("/api/config").json()["data"]["values"]
                        for key in keys:
                            self.assertEqual(values[key], expected)
                        round_trip = client.put("/api/config", json={"values": values})
                        self.assertEqual(round_trip.status_code, 200, round_trip.text)

    def test_pipeline_honors_cancellation_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load_config(DEFAULT_FILE)
            config["runtime"]["state_db"] = Path(directory) / "state.sqlite3"
            adapter = unittest.mock.Mock()
            with patch("video_tool.pipeline.DouyinAdapter", return_value=adapter):
                with self.assertRaises(TaskCancelled):
                    collect_or_run(config, ["https://www.douyin.com/video/123"], False,
                                   cancel_requested=lambda: True)
            adapter.close.assert_called_once()

    def test_local_video_transcription_uses_absolute_path_without_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            source = root / "sample.mp4"
            source.write_bytes(b"video")
            output = root / "sample.txt"
            output.write_text("转写结果", encoding="utf-8")
            config = load_config(root / "config.yaml.bak")
            config["runtime"]["state_db"] = root / "state.sqlite3"
            config["asr"]["api_key"] = "test-key"
            options = {"title": "本地转写", "download_video": False, "transcribe_text": True,
                       "include_comments": False, "generate_report": False,
                       "local_video_path": str(source.resolve())}
            with patch.object(api, "_config", return_value=config), patch.object(api, "_run_task"):
                task = api._create_task(api.TaskRequest(mode="run", urls=[], options=options))
            with patch.object(api, "transcribe_one", return_value=output):
                api._run_task(task["id"], api.TaskRequest(mode="run", urls=[], options=options), config)
            saved = api._get_task(task["id"], config)
            self.assertEqual(saved["state"], "done")
            self.assertEqual(saved["steps"]["collect"], "pending")
            self.assertEqual(saved["steps"]["download"], "pending")
            self.assertEqual(saved["steps"]["transcribe"], "done")
            self.assertEqual(saved["output_path"], str(output.resolve()))

    def test_video_list_filters_by_author_and_title(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            config = load_config(root / "config.yaml.bak")
            config["runtime"]["state_db"] = root / "state.sqlite3"
            store = Store(config["runtime"]["state_db"])
            store.save_video(Video("douyin", "1", "https://www.douyin.com/video/1", "AI 工具",
                                   "author-a", "张三", description="AI 工具的完整介绍",
                                   likes=12, favorites=3, shares=2, published_at="2024-01-15T00:00:00+00:00"))
            store.save_video(Video("douyin", "2", "https://www.douyin.com/video/2", "旅行日记",
                                   "author-b", "李四", description="周末出游",
                                   published_at="2025-01-15T00:00:00+00:00"))
            store.close()
            with patch.object(api, "_config", return_value=config):
                client = TestClient(api.app)
                result = client.get("/api/videos", params={"author": "张", "title": "工具",
                                                            "published_from": "2024-01-01", "published_to": "2024-12-31",
                                                            "source_url": "https://www.douyin.com/video/1"})
            self.assertEqual(result.status_code, 200, result.text)
            payload = result.json()["data"]
            self.assertEqual(payload["total"], 1)
            self.assertEqual(payload["items"][0]["video_id"], "1")
            self.assertEqual(payload["items"][0]["favorites"], 3)


if __name__ == "__main__":
    unittest.main()
