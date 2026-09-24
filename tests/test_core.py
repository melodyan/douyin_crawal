from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import yaml

from video_tool.config import DEFAULT_FILE, _walk, default_config_path, input_urls, load_config, parser_for, validate
from video_tool.models import Comment, CommentResult, Discovery, SegmentResult, Video
from video_tool.downloader import DownloadResult
from video_tool.pipeline import collect_or_run
from video_tool.platforms.douyin import DouyinAdapter, _comment_objects, likes_count
from video_tool.reporter import generate
from video_tool.storage import Store
from video_tool.transcriber import Transcriber, merge_text


class CoreTests(unittest.TestCase):
    def test_local_config_takes_precedence_without_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(default_config_path(root), root / "config.yaml")
            (root / "config.local.yaml").write_text("platform: douyin\n", encoding="utf-8")
            self.assertEqual(default_config_path(root), root / "config.local.yaml")

    def test_cli_overrides_each_config_leaf(self):
        defaults = yaml.safe_load(DEFAULT_FILE.read_text(encoding="utf-8"))
        parser = parser_for(defaults)
        flags = {option for action in parser._actions for option in action.option_strings}
        for name, _ in _walk(defaults):
            expected_flag = {"inputs.urls": "--url", "asr.api_key": "--mimo-api-key"}.get(
                name, "--" + name.replace(".", "-").replace("_", "-"))
            self.assertIn(expected_flag, flags, name)
        expected = ["--url", "--urls-file", "--inputs-max-videos-per-profile",
                    "--browser-headless", "--no-browser-headless", "--mimo-api-key",
                    "--report-output-dir", "--runtime-state-db"]
        self.assertTrue(set(expected) <= flags)
        args = parser.parse_args(["--url", "https://www.douyin.com/video/1", "--no-browser-headless",
                                  "--report-top-comments", "20", "--urls-file", "inputs.txt"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.yaml").write_text(DEFAULT_FILE.read_text(encoding="utf-8"), encoding="utf-8")
            (root / "inputs.txt").write_text("https://www.douyin.com/video/2\nhttps://www.douyin.com/video/1\n", encoding="utf-8")
            config = load_config(root / "config.yaml", args)
            self.assertEqual(config["report"]["top_comments"], 20)
            self.assertEqual(config["inputs"]["urls_file"], root / "inputs.txt")
            self.assertEqual(len(input_urls(config)), 2)
            self.assertFalse(config["browser"]["headless"])
            validate(config, "collect", input_urls(config))

    def test_command_specific_validation(self):
        config = load_config(DEFAULT_FILE)
        config["platform"] = "xiaohongshu"
        with self.assertRaisesRegex(ValueError, "暂未支持"):
            validate(config, "collect", ["https://www.douyin.com/video/1"])
        config["platform"] = "douyin"
        validate(config, "report", [])
        validate(config, "download", ["https://www.douyin.com/video/1"])
        with self.assertRaisesRegex(ValueError, "不是抖音"):
            validate(config, "collect", ["https://www.bilibili.com/video/1"])

    def test_store_report_and_comment_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            video = Video("douyin", "123", "https://www.douyin.com/video/123", "标题 #话题",
                          "author1", "测试作者", ["话题"], "present", transcript_status="failed",
                          transcript_error="媒体不可得")
            store.save_video(video)
            comments = [Comment(str(index), f"评论 {index} | <script>", index, "用户") for index in range(70)]
            store.save_comments("douyin", "123", CommentResult(comments, False, "达到评论上限 70"))
            store.save_discovery("douyin", "https://www.douyin.com/user/author1",
                                 Discovery([video.url], False, "连续滚动无新视频", "author1", "测试作者"))
            paths = generate(store, {"output_dir": root / "reports", "top_comments": 50,
                                     "include_comment_author": False, "write_partial_results": True})
            content = paths[0].read_text(encoding="utf-8")
            self.assertIn("已采集评论的点赞前 50 条一级评论", content)
            self.assertIn("采集数量：70", content)
            self.assertIn("媒体不可得", content)
            self.assertIn("&lt;script", content)
            self.assertEqual(content.count(". 赞 "), 50)
            store.close()

    def test_comment_parser_and_merge(self):
        payload = {"comments": [{"cid": "1", "text": "一级", "reply_comment_total": 2, "reply_id": "0"},
                                {"cid": "2", "text": "回复", "reply_id": "1"}]}
        self.assertEqual([item["cid"] for item in _comment_objects(payload)], ["1"])
        self.assertEqual(likes_count("1.2万"), 12000)
        self.assertEqual(merge_text("你好世界", "世界真好"), "你好世界真好")

    def test_inaccessible_profile_still_gets_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            store.save_discovery("douyin", "https://www.douyin.com/user/abc",
                                 Discovery([], False, "服务异常", "abc", "作者"))
            paths = generate(store, {"output_dir": root / "reports", "top_comments": 50,
                                     "include_comment_author": False, "write_partial_results": True})
            self.assertEqual(len(paths), 1)
            self.assertIn("服务异常", paths[0].read_text(encoding="utf-8"))
            recovered = store.get_discovery("douyin", "https://www.douyin.com/user/abc")
            self.assertEqual(recovered.video_urls, [])
            store.close()

    def test_discovery_checkpoint_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "state.sqlite3")
            url = "https://www.douyin.com/user/abc"
            store.save_discovery("douyin", url, Discovery(["https://www.douyin.com/video/1"],
                                                       False, "采集中", "abc", "作者"))
            store.close()
            reopened = Store(Path(directory) / "state.sqlite3")
            checkpoint = reopened.get_discovery("douyin", url)
            self.assertEqual(checkpoint.video_urls, ["https://www.douyin.com/video/1"])
            self.assertFalse(checkpoint.complete)
            reopened.close()

    def test_oversize_audio_is_split_before_request(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.mp4"
            source.write_bytes(b"fake")
            transcriber = Transcriber({"ffmpeg_path": "ffmpeg", "mp3_bitrate_kbps": 64},
                                      {"chunk_seconds": 4, "overlap_seconds": 0, "max_retries": 0})
            transcriber._probe = lambda _: 4
            transcriber._extract = lambda _, path, start, end: path.write_bytes(b"x" * (12 if end-start > 2 else 3))
            transcriber._request = lambda _: "完成"
            with patch("video_tool.transcriber.MAX_BASE64_BYTES", 10):
                text, segments = transcriber.transcribe(source)
            self.assertEqual(len(segments), 2)
            self.assertTrue(all(item.status == "complete" for item in segments))
            self.assertEqual(text, "完成")

    def test_mimo_timeout_retries_once(self):
        transcriber = Transcriber({}, {"base_url": "https://api.xiaomimimo.com/v1",
                                        "api_key": "test", "model": "mimo-v2.5-asr",
                                        "language": "auto", "timeout_seconds": 2, "max_retries": 1})
        response = httpx.Response(200, json={"choices": [{"message": {"content": "识别完成"}}]},
                                  request=httpx.Request("POST", "https://api.xiaomimimo.com/v1/chat/completions"))
        with patch("video_tool.transcriber.httpx.post", side_effect=[httpx.TimeoutException("timeout"), response]) as post:
            with patch("video_tool.transcriber.time.sleep"):
                self.assertEqual(transcriber._request("abcd"), "识别完成")
        self.assertEqual(post.call_count, 2)

    def test_missing_ffmpeg_has_actionable_error(self):
        transcriber = Transcriber({"ffmpeg_path": "ffmpeg"}, {})
        with patch("video_tool.transcriber.shutil.which", return_value=None):
            with self.assertRaisesRegex(FileNotFoundError, "audio.ffmpeg_path"):
                transcriber.check_dependencies()

    def test_missing_ffprobe_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            ffmpeg = Path(directory) / "ffmpeg.exe"
            ffmpeg.write_bytes(b"fake")
            transcriber = Transcriber({"ffmpeg_path": str(ffmpeg)}, {})
            with patch("video_tool.transcriber.shutil.which", side_effect=[str(ffmpeg), None]):
                with self.assertRaisesRegex(FileNotFoundError, "FFprobe"):
                    transcriber.check_dependencies()

    def test_blocked_ffmpeg_is_detected_before_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ffmpeg = root / "ffmpeg.exe"
            ffprobe = root / "ffprobe.exe"
            ffmpeg.write_bytes(b"fake")
            ffprobe.write_bytes(b"fake")
            transcriber = Transcriber({"ffmpeg_path": str(root)}, {})
            with patch("video_tool.transcriber.shutil.which", return_value=str(ffmpeg)), \
                 patch("video_tool.transcriber.subprocess.run",
                       side_effect=OSError(4551, "应用程序控制策略已阻止此文件")):
                with self.assertRaisesRegex(RuntimeError, "应用控制策略阻止运行"):
                    transcriber.check_dependencies()

    def test_profile_discovery_ignores_unrelated_aweme(self):
        class Locator:
            def evaluate_all(self, _):
                return []

        class Page:
            def locator(self, _):
                return Locator()

        adapter = DouyinAdapter.__new__(DouyinAdapter)
        adapter.page = Page()
        adapter.responses = [
            ("https://www.douyin.com/aweme/v1/web/aweme/post/",
             {"aweme_list": [{"aweme_id": "111", "desc": "own"}]}),
            ("https://www.douyin.com/aweme/v1/web/recommend/aweme/",
             {"aweme_list": [{"aweme_id": "222", "desc": "other"}]}),
        ]
        self.assertEqual(adapter._visible_video_urls(profile=True), ["https://www.douyin.com/video/111"])

    def test_run_pipeline_writes_report_and_resumes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = load_config(DEFAULT_FILE)
            config["runtime"]["state_db"] = root / "state.sqlite3"
            config["report"]["output_dir"] = root / "reports"
            config["download"]["output_dir"] = root / "downloads"
            media = root / "downloads" / "123.mp4"
            media.parent.mkdir()
            media.write_bytes(b"media")
            video = Video("douyin", "123", "https://www.douyin.com/video/123", "标题",
                          "author", "作者", tags_status="absent", media_urls=["https://media.example/v.mp4"],
                          metadata_status="complete")
            adapter = Mock()
            adapter.read_video.return_value = video
            adapter.read_comments.return_value = CommentResult([Comment("1", "评论", 2)], True, "到底")
            with patch("video_tool.pipeline.DouyinAdapter", return_value=adapter), \
                 patch("video_tool.pipeline.download", return_value=DownloadResult(media, "video/mp4")), \
                 patch("video_tool.pipeline.Transcriber") as asr:
                asr.return_value.transcribe.return_value = ("转写内容", [SegmentResult(0, 0, 1, "complete", "转写内容")])
                paths = collect_or_run(config, [video.url], True)
                self.assertIn("转写内容", paths[0].read_text(encoding="utf-8"))
                self.assertFalse(media.exists())
                collect_or_run(config, [video.url], True)
                self.assertEqual(adapter.read_video.call_count, 1)

    def test_missing_ffmpeg_is_logged_before_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = load_config(DEFAULT_FILE)
            config["runtime"]["state_db"] = root / "state.sqlite3"
            config["report"]["output_dir"] = root / "reports"
            video = Video("douyin", "123", "https://www.douyin.com/video/123", "标题",
                          "author", "作者", tags_status="absent", media_urls=["https://media.example/v.mp4"],
                          metadata_status="complete")
            adapter = Mock()
            adapter.read_video.return_value = video
            adapter.read_comments.return_value = CommentResult([], True, "到底")
            with patch("video_tool.pipeline.DouyinAdapter", return_value=adapter), \
                 patch("video_tool.pipeline.download") as mocked_download, \
                 patch("video_tool.pipeline.Transcriber") as mocked_asr:
                mocked_asr.return_value.check_dependencies.side_effect = FileNotFoundError("找不到 FFmpeg：ffmpeg")
                with self.assertLogs("video_tool.pipeline", level="ERROR") as logs:
                    status = {}
                    paths = collect_or_run(config, [video.url], True, status)
            mocked_download.assert_not_called()
            self.assertEqual(status["failed"], 1)
            self.assertIn("转写前检查失败", "\n".join(logs.output))
            self.assertIn("找不到 FFmpeg", paths[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
