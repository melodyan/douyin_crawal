from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import yaml

from video_tool.config import DEFAULT_FILE, _walk, default_config_path, input_urls, load_config, parser_for, validate
from video_tool.models import Comment, CommentResult, Discovery, SegmentResult, Video
from video_tool.downloader import DownloadResult, download_images
from video_tool.pipeline import collect_or_run, download_one
from video_tool.platforms.douyin import DouyinAdapter, _comment_objects, likes_count, video_id
from video_tool.reporter import generate
from video_tool.storage import Store
from video_tool.transcriber import Transcriber, merge_text


class CoreTests(unittest.TestCase):
    def test_local_config_takes_precedence_without_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(default_config_path(root), root / "config.yaml.bak")
            (root / "config.yaml").write_text("platform: douyin\n", encoding="utf-8")
            self.assertEqual(default_config_path(root), root / "config.yaml")

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
            (root / "config.yaml.bak").write_text(DEFAULT_FILE.read_text(encoding="utf-8"), encoding="utf-8")
            (root / "inputs.txt").write_text("https://www.douyin.com/video/2\nhttps://www.douyin.com/video/1\n", encoding="utf-8")
            config = load_config(root / "config.yaml.bak", args)
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

    def test_note_metadata_uses_album_images_in_order(self):
        item = {"aweme_id": "123", "aweme_type": 68, "desc": "图文标题", "author": {"uid": "a", "nickname": "作者"},
                "images": [{"url_list": ["https://cdn.example/1a", "https://cdn.example/1b"]},
                           {"url": {"url_list": ["https://cdn.example/2"]}}],
                "video": {"play_addr": {"url_list": ["https://cdn.example/music"]}}}
        adapter = DouyinAdapter.__new__(DouyinAdapter)
        adapter._navigate = lambda _: "https://www.douyin.com/note/123"
        adapter.responses = [("https://www.douyin.com/aweme/detail", {"aweme_detail": item})]
        adapter._hydration = lambda: []
        adapter.page = Mock()
        post = adapter.read_video("https://www.douyin.com/note/123")
        self.assertEqual(video_id(post.url), "123")
        self.assertEqual(post.content_type, "image")
        self.assertEqual(post.image_urls, [["https://cdn.example/1a", "https://cdn.example/1b"],
                                           ["https://cdn.example/2"]])
        self.assertEqual(post.media_urls, [])
        self.assertEqual(post.metadata_status, "complete")

    def test_note_uses_rendered_album_when_aweme_images_are_missing(self):
        adapter = DouyinAdapter.__new__(DouyinAdapter)
        adapter._navigate = lambda _: "https://www.douyin.com/note/123"
        adapter.responses = [("https://www.douyin.com/aweme/detail",
                              {"aweme_detail": {"aweme_id": "123", "aweme_type": 68,
                                                "desc": "", "author": {"uid": "a"}}})]
        adapter._hydration = lambda: []
        adapter.page = Mock()
        adapter.page.title.return_value = "图文标题 - 抖音"
        adapter.page.locator.return_value.first.inner_text.side_effect = RuntimeError("no heading")
        adapter.page.locator.return_value.first.get_attribute.side_effect = RuntimeError("no meta")
        adapter.page.locator.return_value.evaluate_all.return_value = [
            ["https://cdn.example/1", "https://cdn.example/1"], ["https://cdn.example/2"]]
        post = adapter.read_video("https://www.douyin.com/note/123")
        self.assertEqual(post.title, "图文标题")
        self.assertEqual(post.image_urls, [["https://cdn.example/1"], ["https://cdn.example/2"]])
        self.assertEqual(post.metadata_status, "complete")

    def test_profile_note_is_not_duplicated_by_video_link(self):
        class Locator:
            def evaluate_all(self, _):
                return ["https://www.douyin.com/video/123"]

        adapter = DouyinAdapter.__new__(DouyinAdapter)
        adapter.page = Mock()
        adapter.page.locator.return_value = Locator()
        adapter.responses = [("https://www.douyin.com/aweme/v1/web/aweme/post/",
                              {"aweme_list": [{"aweme_id": "123", "aweme_type": 68,
                                               "images": [{"url_list": ["https://cdn.example/1"]}]}]})]
        self.assertEqual(adapter._visible_video_urls(profile=True), ["https://www.douyin.com/note/123"])

    def test_download_images_retries_bad_source_and_saves_real_extensions(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "123"
            def fake_download(urls, destination, _):
                data = {"https://cdn.example/bad": b"<html>blocked</html>",
                        "https://cdn.example/jpeg": b"\xff\xd8\xffphoto",
                        "https://cdn.example/png": b"\x89PNG\r\n\x1a\nphoto"}[urls[0]]
                destination.write_bytes(data)
                return DownloadResult(destination, "application/octet-stream")
            with patch("video_tool.downloader.download", side_effect=fake_download):
                result = download_images([["https://cdn.example/bad", "https://cdn.example/jpeg"],
                                          ["https://cdn.example/png"]], folder, 5)
            self.assertEqual(result, folder)
            self.assertEqual([path.name for path in sorted(folder.iterdir())], ["01.jpg", "02.png"])
            self.assertEqual((folder / "01.jpg").read_bytes(), b"\xff\xd8\xffphoto")

    def test_download_command_selects_note_images(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load_config(DEFAULT_FILE)
            config["download"]["output_dir"] = Path(directory)
            post = Video("douyin", "123", "https://www.douyin.com/note/123", content_type="image",
                         image_urls=[["https://cdn.example/1"]])
            adapter = Mock()
            adapter.read_video.return_value = post
            with patch("video_tool.pipeline.DouyinAdapter", return_value=adapter), \
                 patch("video_tool.pipeline.download_images", return_value=Path(directory) / "123") as images, \
                 patch("video_tool.pipeline.download") as video_download:
                result = download_one(config, post.url)
            self.assertEqual(result, Path(directory) / "123")
            images.assert_called_once()
            video_download.assert_not_called()

    def test_run_downloads_note_without_transcriber(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = load_config(DEFAULT_FILE)
            config["runtime"]["state_db"] = root / "state.sqlite3"
            config["report"]["output_dir"] = root / "reports"
            config["download"]["output_dir"] = root / "downloads"
            config["asr"]["api_key"] = ""
            post = Video("douyin", "123", "https://www.douyin.com/note/123", "图文标题",
                         "author", "作者", content_type="image", image_urls=[["https://cdn.example/1"]],
                         metadata_status="complete")
            adapter = Mock()
            adapter.read_video.return_value = post
            adapter.read_comments.return_value = CommentResult([], True, "到底")
            with patch("video_tool.pipeline.DouyinAdapter", return_value=adapter), \
                 patch("video_tool.pipeline.download_images", return_value=root / "downloads" / "123") as images, \
                 patch("video_tool.pipeline.Transcriber") as asr:
                status = {}
                reports = collect_or_run(config, [post.url], True, status)
            self.assertEqual(status["failed"], 0)
            images.assert_called_once()
            asr.assert_not_called()
            self.assertIn("图片状态：images\\_saved", reports[0].read_text(encoding="utf-8"))

    def test_run_pipeline_writes_report_and_resumes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = load_config(DEFAULT_FILE)
            config["runtime"]["state_db"] = root / "state.sqlite3"
            config["report"]["output_dir"] = root / "reports"
            config["download"]["output_dir"] = root / "downloads"
            config["asr"]["api_key"] = "test"
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
            config["asr"]["api_key"] = "test"
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
