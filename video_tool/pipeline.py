"""Command orchestration; independent modules stay importable."""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

from .downloader import download
from .models import CommentResult, Video
from .platforms.douyin import DouyinAdapter, is_profile, video_id
from .reporter import generate
from .storage import Store
from .transcriber import Transcriber

LOG = logging.getLogger(__name__)


def _preserve(new: Video, old: Video | None):
    if old:
        new.comments_complete = old.comments_complete
        new.comments_stop_reason = old.comments_stop_reason
        new.transcript_status = old.transcript_status
        new.transcript = old.transcript
        new.transcript_error = old.transcript_error


def _process_video(adapter: DouyinAdapter, store: Store, url: str, config: dict, full: bool) -> bool:
    identifier = video_id(url)
    old = store.get_video("douyin", identifier) if identifier else None
    if old and config["crawl"]["resume"] and not config["crawl"]["refresh_completed"]:
        if old.metadata_status == "complete" and old.comments_complete and (not full or old.transcript_status == "complete"):
            LOG.info("跳过已完成视频 %s", old.video_id)
            return True
    try:
        video = adapter.read_video(url)
    except Exception as exc:
        LOG.error("视频采集失败 %s: %s", url, exc)
        if identifier:
            failed = old or Video("douyin", identifier, url)
            failed.metadata_status, failed.metadata_error = "failed", str(exc)
            store.save_video(failed)
        return False
    if not config["crawl"]["refresh_completed"]:
        _preserve(video, old)
    store.save_video(video)
    comments_failed = False
    if not (old and old.comments_complete and config["crawl"]["resume"] and not config["crawl"]["refresh_completed"]):
        try:
            result = adapter.read_comments(video, config["crawl"]["max_comments_per_video"])
        except Exception as exc:
            LOG.error("评论采集失败 %s: %s", video.video_id, exc)
            result = CommentResult([], False, str(exc))
            comments_failed = True
        store.save_comments("douyin", video.video_id, result, replace=config["crawl"]["refresh_completed"])
        video.comments_complete = result.complete
        video.comments_stop_reason = result.stop_reason
    if not full or (old and old.transcript_status == "complete" and config["crawl"]["resume"] and not config["crawl"]["refresh_completed"]):
        return video.metadata_status == "complete" and not comments_failed
    transcriber = Transcriber(config["audio"], config["asr"])
    try:
        transcriber.check_dependencies()
    except (OSError, ValueError, RuntimeError) as exc:
        video.transcript_status, video.transcript_error = "failed", str(exc)
        store.save_video(video)
        LOG.error("视频 %s 转写前检查失败：%s", video.video_id, exc)
        return False
    media_path = Path(config["download"]["output_dir"]) / f"{video.video_id}.mp4"
    LOG.info("视频 %s 开始下载临时媒体", video.video_id)
    result = download(video.media_urls, media_path, config["audio"]["download_timeout_seconds"])
    if not result.path:
        video.transcript_status, video.transcript_error = "failed", result.error
        store.save_video(video)
        LOG.error("视频 %s 下载失败：%s", video.video_id, result.error)
        return False
    LOG.info("视频 %s 已下载临时媒体：%s", video.video_id, result.path)
    try:
        text, segments = transcriber.transcribe(
            result.path,
            on_segment=lambda parts: store.save_segments("douyin", video.video_id, parts),
            existing=store.segments("douyin", video.video_id)
                     if config["crawl"]["resume"] and not config["crawl"]["refresh_completed"] else None)
        video.transcript = text
        failures = [part.error for part in segments if part.status != "complete"]
        video.transcript_status = "partial" if failures and text else "failed" if failures else "complete"
        video.transcript_error = "; ".join(failures)
        if failures:
            LOG.error("视频 %s 转写%s：%s", video.video_id, video.transcript_status, video.transcript_error)
        else:
            LOG.info("视频 %s 转写完成", video.video_id)
    except Exception as exc:
        video.transcript_status, video.transcript_error = "failed", str(exc)
        LOG.error("视频 %s 转写失败：%s", video.video_id, exc)
    finally:
        if not config["audio"]["keep_temporary_files"]:
            result.path.unlink(missing_ok=True)
            LOG.info("视频 %s 已删除临时媒体（audio.keep_temporary_files=false）", video.video_id)
        else:
            LOG.info("视频 %s 已保留媒体文件：%s", video.video_id, result.path)
    store.save_video(video)
    return video.transcript_status == "complete" and not comments_failed


def collect_or_run(config: dict, urls: list[str], full: bool, status: dict[str, int] | None = None) -> list[Path]:
    store = Store(Path(config["runtime"]["state_db"]))
    adapter = None
    failures = 0
    try:
        adapter = DouyinAdapter(config["browser"], config["crawl"])
        for input_url in urls:
            try:
                resolved = input_url
                profile = is_profile(input_url)
                if not profile and not video_id(input_url):
                    resolved, profile = adapter.resolve_kind(input_url)
                if profile:
                    previous = store.get_discovery("douyin", resolved) if config["crawl"]["resume"] else None
                    if previous and previous.complete and not config["crawl"]["refresh_completed"]:
                        discovery = previous
                    else:
                        discovery = adapter.discover(
                            resolved, config["inputs"]["max_videos_per_profile"], previous,
                            on_progress=lambda partial: store.save_discovery("douyin", resolved, partial))
                    store.save_discovery("douyin", resolved, discovery)
                    LOG.info("主页发现 %s：%s 条，%s，%s", resolved, len(discovery.video_urls),
                             "完整" if discovery.complete else "未确认完整", discovery.stop_reason)
                    targets = discovery.video_urls
                else:
                    targets = [resolved]
                for target in targets:
                    if not _process_video(adapter, store, target, config, full):
                        failures += 1
                if profile and not targets and not discovery.complete:
                    failures += 1
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                failures += 1
                LOG.error("输入处理失败 %s: %s", input_url, exc)
                if is_profile(input_url):
                    from .models import Discovery
                    author_id = urlparse(input_url).path.rstrip("/").split("/")[-1]
                    store.save_discovery("douyin", input_url, Discovery([], False, str(exc), author_id))
        if status is not None:
            status["failed"] = failures
        return generate(store, config["report"]) if full else []
    finally:
        if adapter:
            adapter.close()
        store.close()


def download_one(config: dict, url: str) -> Path:
    adapter = DouyinAdapter(config["browser"], config["crawl"])
    try:
        video = adapter.read_video(url)
        destination = Path(config["download"]["output_dir"]) / f"{video.video_id}.mp4"
        result = download(video.media_urls, destination, config["audio"]["download_timeout_seconds"])
        if not result.path:
            raise RuntimeError(result.error)
        return result.path
    finally:
        adapter.close()


def transcribe_one(config: dict, source: Path) -> Path:
    transcriber = Transcriber(config["audio"], config["asr"])
    transcriber.check_dependencies()
    text, segments = transcriber.transcribe(source)
    failures = [part.error for part in segments if part.status != "complete"]
    if failures:
        raise RuntimeError("转写片段失败: " + "; ".join(failures))
    output = Path(config["asr"]["output_dir"])
    output.mkdir(parents=True, exist_ok=True)
    path = output / (source.stem + ".txt")
    path.write_text(text, encoding="utf-8")
    return path


def report_only(config: dict) -> list[Path]:
    store = Store(Path(config["runtime"]["state_db"]))
    try:
        return generate(store, config["report"])
    finally:
        store.close()
