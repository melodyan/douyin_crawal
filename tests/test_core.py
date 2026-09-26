from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import yaml

from video_tool.config import DEFAULT_FILE, _walk, default_config_path, input_urls, load_config, parser_for, validate
from video_tool.models import Collection, Comment, CommentResult, Discovery, SegmentResult, Video
from video_tool.downloader import DownloadResult, download_images
from video_tool.naming import safe_filename, video_stem
from video_tool.pipeline import collect_or_run, download_one
from video_tool.platforms.douyin import DouyinAdapter, _comment_objects, _video_sources, collection_id, likes_count, video_id
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
                    "--inputs-max-videos-per-collection",
                    "--browser-headless", "--no-browser-headless", "--mimo-api-key",
                    "--report-output-dir", "--runtime-state-db"]
        self.assertTrue(set(expected) <= flags)
        args = parser.parse_args(["--url", "https://www.douyin.com/video/1", "--no-browser-headless",
                                  "--report-top-comments", "20", "--urls-file", "inputs.txt",
                                  "--inputs-max-videos-per-collection", "2"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.yaml.bak").write_text(DEFAULT_FILE.read_text(encoding="utf-8"), encoding="utf-8")
            (root / "inputs.txt").write_text("https://www.douyin.com/video/2\nhttps://www.douyin.com/video/1\n", encoding="utf-8")
            config = load_config(root / "config.yaml.bak", args)
            self.assertEqual(config["report"]["top_comments"], 20)
            self.assertEqual(config["inputs"]["max_videos_per_collection"], 2)
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
            self.assertIn("报告列出：50 条一级评论、0 条回复", content)
            self.assertIn("采集数量：70 条一级评论", content)
            self.assertIn("媒体不可得", content)
            self.assertIn("&lt;script", content)
            self.assertEqual(content.count(". 一级评论 · 赞 "), 50)
            store.close()

    def test_comment_parser_and_merge(self):
        payload = {"comments": [{"cid": "1", "text": "一级", "reply_comment_total": 2, "reply_id": "0"},
                                {"cid": "2", "text": "回复", "reply_id": "1"}]}
        self.assertEqual([item["cid"] for item in _comment_objects(payload)], ["1", "2"])
        self.assertEqual(likes_count("1.2万"), 12000)
        self.assertEqual(merge_text("你好世界", "世界真好"), "你好世界真好")

    def test_replies_are_collected_and_incomplete_replies_are_reported(self):
        adapter = DouyinAdapter.__new__(DouyinAdapter)
        adapter.page = Mock()
        adapter.page.url = "https://www.douyin.com/video/123"
        adapter.page.get_by_text.return_value.all.return_value = []
        adapter.crawl = {"no_new_content_scrolls": 1, "delay_seconds": 0}
        main_url = "https://www.douyin.com/aweme/v1/web/comment/list/?aweme_id=123"
        reply_url = "https://www.douyin.com/aweme/v1/web/comment/list/reply/?aweme_id=123&comment_id=1"
        main = {"status_code": 0, "has_more": 0, "comments": [
            {"cid": "1", "text": "一级", "reply_id": "0", "reply_comment_total": 2}]}
        reply = {"status_code": 0, "has_more": 0, "comments": [
            {"cid": "2", "text": "回复一", "reply_id": "1"},
            {"cid": "3", "text": "回复二", "reply_id": "1", "reply_to_reply_id": "2"}]}
        adapter.responses = [(main_url, main), (reply_url, reply)]
        result = adapter.read_comments(Video("douyin", "123", adapter.page.url), None)
        self.assertTrue(result.complete)
        self.assertEqual({item.comment_id: item.parent_id for item in result.comments},
                         {"1": "", "2": "1", "3": "1"})
        adapter.responses = [(main_url, main)]
        result = adapter.read_comments(Video("douyin", "123", adapter.page.url), None)
        self.assertFalse(result.complete)
        self.assertIn("2 条回复未采集", result.stop_reason)
        adapter.responses = [(main_url, main), (reply_url, {**reply, "has_more": 1})]
        result = adapter.read_comments(Video("douyin", "123", adapter.page.url), None)
        self.assertFalse(result.complete)
        self.assertIn("1 个回复列表未到底", result.stop_reason)

    def test_comment_scroll_loads_root_and_expand_more_loads_all_replies(self):
        adapter = DouyinAdapter.__new__(DouyinAdapter)
        adapter.crawl = {"no_new_content_scrolls": 3, "delay_seconds": 0}
        adapter.responses = []
        adapter.page = Mock()
        adapter.page.url = "https://www.douyin.com/video/123"
        main_url = "https://www.douyin.com/aweme/v1/web/comment/list/?aweme_id=123"
        reply_url = "https://www.douyin.com/aweme/v1/web/comment/list/reply/?aweme_id=123&comment_id=1"
        main = {"status_code": 0, "has_more": 0, "comments": [
            {"cid": "1", "text": "一级评论", "reply_id": "0", "reply_comment_total": 2}]}
        replies = [
            {"status_code": 0, "has_more": 1, "comments": [
                {"cid": "2", "text": "回复一", "reply_id": "1"}]},
            {"status_code": 0, "has_more": 0, "comments": [
                {"cid": "3", "text": "回复二", "reply_id": "1"}]},
        ]
        route = Mock()
        route.first.count.return_value = 1
        route.first.evaluate.side_effect = lambda _: adapter.responses.append((main_url, main))
        button = Mock()
        button.is_visible.return_value = True
        button.click.side_effect = lambda **_: adapter.responses.append((reply_url, replies.pop(0)))
        comment_items = Mock()
        comment_items.filter.return_value.locator.return_value.filter.return_value.all.return_value = [button]
        hidden = Mock()
        hidden.first.is_visible.return_value = False
        adapter.page.locator.side_effect = lambda selector: (
            route if selector == ".route-scroll-container" else
            comment_items if selector == '[data-e2e="comment-item"]' else hidden)
        progress = Mock()
        result = adapter.read_comments(Video("douyin", "123", adapter.page.url), 1,
                                       on_progress=progress)
        self.assertEqual(len(result.comments), 3)
        self.assertEqual(sum(bool(item.parent_id) for item in result.comments), 2)
        self.assertEqual(button.click.call_count, 2)
        route.first.evaluate.assert_called_once()
        self.assertEqual(progress.call_count, 3)
        self.assertTrue(all(not call.args[0].complete for call in progress.call_args_list))
        pattern = comment_items.filter.return_value.locator.return_value.filter.call_args.kwargs["has_text"]
        self.assertIsNotNone(pattern.search("展开更多"))

    def test_report_is_one_file_per_video_with_all_replies_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "state.sqlite3")
            for identifier in ("123", "456"):
                store.save_video(Video("douyin", identifier, f"https://www.douyin.com/video/{identifier}",
                                       f"标题{identifier}", "same_author", "作者"))
            store.save_comments("douyin", "123", CommentResult([
                Comment("1", "一级", 1), Comment("2", "回复", 2, parent_id="1", reply_to_id="1")], True, "到底"))
            self.assertEqual(store.comments("douyin", "123")[0].reply_to_id, "1")
            reports = root / "reports"
            reports.mkdir()
            (reports / "douyin_123.md").write_text("# 标题123 · 123\n旧报告", encoding="utf-8")
            paths = generate(store, {"output_dir": root / "reports", "top_comments": None,
                                     "include_comment_author": False, "write_partial_results": True})
            self.assertEqual({path.name for path in paths}, {"标题123_123.md", "标题456_456.md"})
            self.assertFalse((reports / "douyin_123.md").exists())
            first = (reports / "标题123_123.md").read_text(encoding="utf-8")
            second = (reports / "标题456_456.md").read_text(encoding="utf-8")
            self.assertIn("回复", first)
            self.assertIn("报告列出：1 条一级评论、1 条回复", first)
            self.assertNotIn("标题456", first)
            self.assertNotIn("标题123", second)
            store.close()

    def test_title_based_filename_is_safe_and_distinguishes_duplicate_titles(self):
        first = Video("douyin", "123", "https://www.douyin.com/video/123", "同名/标题:*?")
        second = Video("douyin", "456", "https://www.douyin.com/video/456", "同名/标题:*?")
        self.assertEqual(video_stem(first), "同名_标题____123")
        self.assertNotEqual(video_stem(first), video_stem(second))
        self.assertEqual(safe_filename("CON"), "_CON")

    def test_existing_database_marks_old_comments_for_reply_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE videos (platform TEXT, video_id TEXT, url TEXT, title TEXT, "
                               "author_id TEXT, author_name TEXT, hashtags TEXT, tags_status TEXT, "
                               "metadata_status TEXT, metadata_error TEXT, comments_complete INTEGER, "
                               "comments_stop_reason TEXT, transcript_status TEXT, transcript TEXT, "
                               "transcript_error TEXT, content_type TEXT, image_urls TEXT, updated_at TEXT, "
                               "PRIMARY KEY (platform, video_id))")
            connection.execute("INSERT INTO videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               ("douyin", "123", "https://www.douyin.com/video/123", "标题", "a", "作者",
                                "[]", "absent", "complete", "", 1, "到底", "complete", "转写", "",
                                "video", "[]", ""))
            connection.commit()
            connection.close()
            store = Store(path)
            self.assertEqual(store.get_video("douyin", "123").comments_version, 1)
            store.close()
            config = load_config(DEFAULT_FILE)
            config["runtime"]["state_db"] = path
            old = Video("douyin", "123", "https://www.douyin.com/video/123", "标题",
                        "a", "作者", metadata_status="complete")
            adapter = Mock()
            adapter.read_video.return_value = old
            adapter.read_comments.return_value = CommentResult([], True, "到底")
            with patch("video_tool.pipeline.DouyinAdapter", return_value=adapter):
                collect_or_run(config, [old.url], False)
            adapter.read_comments.assert_called_once()
            store = Store(path)
            self.assertEqual(store.get_video("douyin", "123").comments_version, 2)
            store.close()

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

    def test_collection_discovery_follows_visible_pagination_in_order(self):
        identifier = "7682002335945459746"
        url = f"https://www.douyin.com/collection/{identifier}/1"
        api = f"https://www.douyin.com/aweme/v1/web/mix/aweme/?mix_id={identifier}&cursor="
        adapter = DouyinAdapter.__new__(DouyinAdapter)
        adapter.crawl = {"no_new_content_scrolls": 2, "delay_seconds": 0}
        adapter.responses = [(api + "0", {"status_code": 0, "cursor": 2, "has_more": 1,
                                            "aweme_list": [{"aweme_id": "111", "desc": "第一集"},
                                                           {"aweme_id": "222", "desc": "第二集"}]})]
        adapter._navigate = Mock()
        adapter.page = Mock()
        adapter.page.locator.return_value.first.inner_text.return_value = "系列名称"
        button = Mock()
        button.is_visible.return_value = True
        button.click.side_effect = lambda **_: adapter.responses.append(
            (api + "2", {"status_code": 0, "cursor": 3, "has_more": 0,
                          "aweme_list": [{"aweme_id": "222"}, {"aweme_id": "333"}]}))
        adapter.page.get_by_text.return_value.all.side_effect = lambda: [button] if len(adapter.responses) == 1 else []
        checkpoints = []
        result = adapter.discover_collection(url, on_progress=checkpoints.append)
        self.assertEqual(collection_id(url), identifier)
        self.assertTrue(result.complete)
        self.assertEqual(result.video_urls, [f"https://www.douyin.com/video/{item}" for item in ("111", "222", "333")])
        self.assertEqual(result.video_titles["111"], "第一集")
        self.assertEqual(len(checkpoints), 2)
        button.click.assert_called_once()

    def test_collection_input_writes_ordered_index_and_resumes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = load_config(DEFAULT_FILE)
            config["runtime"]["state_db"] = root / "state.sqlite3"
            config["report"]["output_dir"] = root / "reports"
            config["inputs"]["max_videos_per_collection"] = 2
            identifier = "987"
            source = f"https://www.douyin.com/collection/{identifier}/1"
            urls = [f"https://www.douyin.com/video/{item}" for item in ("111", "222", "333")]
            adapter = Mock()
            adapter.discover_collection.return_value = Collection(
                identifier, f"https://www.douyin.com/collection/{identifier}", "测试合集", urls, True, "到底",
                {"111": "第一集 #话题", "222": "第二集", "333": "第三集"})
            adapter.read_video.side_effect = [Video("douyin", item, url,
                                                   f"第{index}集" + (" #话题" if index == 1 else ""),
                                                   metadata_status="complete")
                                              for index, (item, url) in enumerate(zip(("111", "222"), urls), 1)]
            adapter.read_comments.return_value = CommentResult([], True, "到底")
            with patch("video_tool.pipeline.DouyinAdapter", return_value=adapter):
                status = {}
                collect_or_run(config, [source], False, status)
            self.assertEqual(status["failed"], 0)
            self.assertEqual(adapter.read_video.call_count, 2)
            store = Store(root / "state.sqlite3")
            self.assertEqual(store.get_collection("douyin", identifier).video_urls, urls)
            self.assertEqual(store.get_collection("douyin", identifier).video_titles["111"], "第一集 #话题")
            paths = generate(store, config["report"])
            index = next(path for path in paths if path.name == f"douyin_collection_{identifier}.md")
            content = index.read_text(encoding="utf-8")
            self.assertLess(content.index("第1集"), content.index("第2集"))
            self.assertIn("发现作品：3；覆盖：确认完整", content)
            self.assertIn("第三集 · 待采集", content)
            self.assertIn("%23", content)
            store.close()

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

    def test_video_sources_include_bitrate_variants_and_all_matching_records(self):
        url_a = "https://cdn.example/standard.mp4"
        url_b = "https://cdn.example/high.mp4"
        self.assertEqual(_video_sources({"video": {"play_addr": {"url_list": [url_a]},
                                                     "bit_rate": [{"play_addr": {"url_list": [url_b]}}]}}),
                         [url_a, url_b])
        self.assertEqual(_video_sources({"video": {"play_addr": {"url_list": ["//cdn.example/video.mp4"]}}}),
                         ["https://cdn.example/video.mp4"])
        adapter = DouyinAdapter.__new__(DouyinAdapter)
        adapter._navigate = lambda _: "https://www.douyin.com/video/123"
        adapter.responses = [("https://www.douyin.com/aweme/detail", {"aweme_detail": {
            "aweme_id": "123", "desc": "标题", "video": {"play_addr": {"url_list": [url_a]}}}}),
            ("https://www.douyin.com/aweme/detail", {"aweme_detail": {
                "aweme_id": "123", "video": {"bit_rate": [{"play_addr": {"url_list": [url_b]}}]}}})]
        adapter._hydration = lambda: []
        adapter.page = Mock()
        post = adapter.read_video("https://www.douyin.com/video/123")
        self.assertEqual(post.media_urls, [url_a, url_b])
        self.assertEqual(post.title, "标题")

    def test_video_player_source_does_not_need_video_id_in_url(self):
        adapter = DouyinAdapter.__new__(DouyinAdapter)
        adapter._navigate = lambda _: "https://www.douyin.com/video/123"
        adapter.responses = [("https://www.douyin.com/aweme/detail", {"aweme_detail": {
            "aweme_id": "123", "desc": "标题", "video": {}}})]
        adapter._hydration = lambda: []
        adapter.page = Mock()
        adapter.page.locator.return_value.evaluate_all.return_value = ["https://cdn.example/opaque-path.mp4"]
        post = adapter.read_video("https://www.douyin.com/video/123")
        self.assertEqual(post.media_urls, ["https://cdn.example/opaque-path.mp4"])
        self.assertEqual(post.metadata_status, "complete")

    def test_video_reloads_once_when_first_page_has_no_media(self):
        adapter = DouyinAdapter.__new__(DouyinAdapter)
        adapter.page = Mock()
        adapter.page.locator.return_value.first.is_visible.return_value = False
        adapter.page.locator.return_value.evaluate_all.side_effect = RuntimeError("no player")
        adapter._hydration = lambda: []
        visits = []

        def navigate(url):
            visits.append(url)
            media = {} if len(visits) == 1 else {"play_addr": {"url_list": ["https://cdn.example/movie.mp4"]}}
            adapter.responses = [("https://www.douyin.com/aweme/detail", {"aweme_detail": {
                "aweme_id": "123", "desc": "标题", "video": media}})]
            return url

        adapter._navigate = navigate
        post = adapter.read_video("https://www.douyin.com/video/123")
        self.assertEqual(len(visits), 2)
        self.assertEqual(post.media_urls, ["https://cdn.example/movie.mp4"])

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
            post = Video("douyin", "123", "https://www.douyin.com/note/123", "图文标题", content_type="image",
                         image_urls=[["https://cdn.example/1"]])
            adapter = Mock()
            adapter.read_video.return_value = post
            with patch("video_tool.pipeline.DouyinAdapter", return_value=adapter), \
                 patch("video_tool.pipeline.download_images", return_value=Path(directory) / "图文标题_123") as images, \
                 patch("video_tool.pipeline.download") as video_download:
                result = download_one(config, post.url)
            self.assertEqual(result, Path(directory) / "图文标题_123")
            images.assert_called_once()
            self.assertEqual(images.call_args.args[1], Path(directory) / "图文标题_123")
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
            media = root / "downloads" / "标题_123.mp4"
            media.parent.mkdir()
            media.write_bytes(b"media")
            video = Video("douyin", "123", "https://www.douyin.com/video/123", "标题",
                          "author", "作者", tags_status="absent", media_urls=["https://media.example/v.mp4"],
                          metadata_status="complete")
            adapter = Mock()
            adapter.read_video.return_value = video
            adapter.read_comments.return_value = CommentResult([Comment("1", "评论", 2)], True, "到底")
            with patch("video_tool.pipeline.DouyinAdapter", return_value=adapter), \
                 patch("video_tool.pipeline.download", return_value=DownloadResult(media, "video/mp4")) as download_media, \
                 patch("video_tool.pipeline.Transcriber") as asr:
                asr.return_value.transcribe.return_value = ("转写内容", [SegmentResult(0, 0, 1, "complete", "转写内容")])
                paths = collect_or_run(config, [video.url], True)
                self.assertIn("转写内容", paths[0].read_text(encoding="utf-8"))
                self.assertEqual(download_media.call_args.args[1], media)
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
