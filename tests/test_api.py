import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient

import api
from video_tool.config import DEFAULT_FILE, load_config
from video_tool.pipeline import TaskCancelled, collect_or_run
from video_tool.models import Video
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
                preserved = client.put("/api/config", json={"values": {"asr.api_key": ""}})
                self.assertEqual(preserved.status_code, 200, preserved.text)
                raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
                self.assertEqual(raw["asr"]["api_key"], "secret-key")
                rejected = client.put("/api/config", json={"values": {"unknown.option": 1}})
                self.assertEqual(rejected.status_code, 422)

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
                                                            "published_from": "2024-01-01", "published_to": "2024-12-31"})
            self.assertEqual(result.status_code, 200, result.text)
            payload = result.json()["data"]
            self.assertEqual(payload["total"], 1)
            self.assertEqual(payload["items"][0]["video_id"], "1")
            self.assertEqual(payload["items"][0]["favorites"], 3)


if __name__ == "__main__":
    unittest.main()
