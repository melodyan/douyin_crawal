"""Command orchestration; independent modules stay importable."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .downloader import download, download_images
from .models import CommentResult, Video
from .naming import author_directory, video_stem
from .platforms.douyin import DouyinAdapter, collection_id, is_profile, video_id
from .reporter import generate
from .storage import Store
from .transcriber import Transcriber

LOG = logging.getLogger(__name__)


class TaskCancelled(RuntimeError):
    """Raised at a safe checkpoint when an API task requests cancellation."""


def _checkpoint(cancel_requested: Callable[[], bool] | None):
    if cancel_requested and cancel_requested():
        raise TaskCancelled("任务已取消")


def _preserve(new: Video, old: Video | None):
    if old:
        new.comments_complete = old.comments_complete
        new.comments_stop_reason = old.comments_stop_reason
        new.comments_version = old.comments_version
        new.transcript_status = old.transcript_status
        new.transcript = old.transcript
        new.transcript_error = old.transcript_error
        new.local_media_path = old.local_media_path


def _media_done(video: Video) -> bool:
    return video.transcript_status == ("images_saved" if video.content_type == "image" else "complete")


def _log_skipped_media(video: Video, config: dict):
    if video.content_type == "image":
        LOG.info("图文 %s 已保存图片，跳过媒体下载", video.video_id)
        return
    media_path = author_directory(config["download"]["output_dir"], video) / f"{video_stem(video)}.mp4"
    if config["audio"]["keep_temporary_files"] and media_path.is_file():
        LOG.info("视频 %s 已下载并转写完成，媒体文件存在，跳过下载和转写", video.video_id)
    elif config["audio"]["keep_temporary_files"]:
        LOG.info("视频 %s 已下载并转写完成，但媒体文件不存在，跳过下载和转写", video.video_id)
    else:
        LOG.info("视频 %s 已下载并转写完成，临时媒体已按配置删除，跳过下载和转写", video.video_id)


def _process_video(adapter: DouyinAdapter, store: Store, url: str, config: dict, full: bool,
                   include_comments: bool = True,
                   cancel_requested: Callable[[], bool] | None = None,
                   on_progress: Callable[[str, int], None] | None = None,
                   transcribe_media: bool = True,
                   retain_download: bool = False,
                   failed_steps: set[str] | None = None,
                   errors: list[str] | None = None) -> bool:
    failed_steps = failed_steps if failed_steps is not None else set()
    errors = errors if errors is not None else []
    def record_error(step: str, message):
        failed_steps.add(step)
        errors.append(f"[{step}] {message}\n作品链接：{url}")
    _checkpoint(cancel_requested)
    identifier = video_id(url)
    old = store.get_video("douyin", identifier) if identifier else None
    if old and config["crawl"]["refresh_completed"]:
        LOG.info("强制重跑视频 %s，忽略已完成状态", old.video_id)
    if old and config["crawl"]["resume"] and not config["crawl"]["refresh_completed"]:
        if old.content_type == "image":
            media_path = author_directory(config["download"]["output_dir"], old) / video_stem(old)
            media_done = media_path.is_dir()
        else:
            media_path = author_directory(config["download"]["output_dir"], old) / f"{video_stem(old)}.mp4"
            media_done = (_media_done(old) and not retain_download) if transcribe_media else media_path.is_file()
            if transcribe_media and retain_download:
                media_done = _media_done(old) and media_path.is_file()
        comments_done = not include_comments or (old.comments_complete and old.comments_version >= 2)
        if old.metadata_status == "complete" and comments_done and (not full or media_done):
            if full and media_done:
                _log_skipped_media(old, config)
            LOG.info("视频 %s 已完成全部处理，跳过", old.video_id)
            return True
    try:
        video = adapter.read_video(url)
    except Exception as exc:
        LOG.error("视频采集失败 %s: %s", url, exc)
        record_error("collect", str(exc))
        if identifier:
            failed = old or Video("douyin", identifier, url)
            failed.metadata_status, failed.metadata_error = "failed", str(exc)
            store.save_video(failed)
        return False
    _checkpoint(cancel_requested)
    if on_progress:
        on_progress("collect", 25)
    if not config["crawl"]["refresh_completed"]:
        _preserve(video, old)
    store.save_video(video)
    if video.metadata_status != "complete":
        record_error("collect", video.metadata_error or "作品信息采集不完整")
    comments_failed = False
    if include_comments and not (old and old.comments_complete and old.comments_version >= 2
            and config["crawl"]["resume"] and not config["crawl"]["refresh_completed"]):
        try:
            def checkpoint(partial):
                _checkpoint(cancel_requested)
                if not config["crawl"]["refresh_completed"]:
                    store.save_comments("douyin", video.video_id, partial)
            result = adapter.read_comments(video, config["crawl"]["max_comments_per_video"],
                                           on_progress=checkpoint,
                                           existing=store.comments("douyin", video.video_id)
                                           if (config["crawl"]["resume"]
                                               and config["crawl"]["max_comments_per_video"] is None
                                               and not config["crawl"]["refresh_completed"])
                                           else None)
        except TaskCancelled:
            raise
        except Exception as exc:
            LOG.error("评论采集失败 %s: %s", video.video_id, exc)
            result = CommentResult([], False, str(exc))
            comments_failed = True
            record_error("comments", str(exc))
        store.save_comments("douyin", video.video_id, result, replace=config["crawl"]["refresh_completed"])
        video.comments_complete = result.complete
        video.comments_stop_reason = result.stop_reason
        video.comments_version = 2
    _checkpoint(cancel_requested)
    if on_progress:
        on_progress("comments" if include_comments else "collect", 45)
    media_done = bool(old and _media_done(old))
    if not full or (media_done and config["crawl"]["resume"] and not config["crawl"]["refresh_completed"]):
        if full and media_done:
            _log_skipped_media(old, config)
        return video.metadata_status == "complete" and not comments_failed
    if video.content_type == "image":
        try:
            if on_progress:
                on_progress("download", 60)
            folder = author_directory(config["download"]["output_dir"], video) / video_stem(video)
            download_images(video.image_urls, folder, config["audio"]["download_timeout_seconds"])
            video.local_media_path = str(folder.resolve())
            video.transcript_status, video.transcript_error = "images_saved", ""
            LOG.info("图文 %s 已保存图片：%s", video.video_id, folder)
        except (OSError, RuntimeError) as exc:
            video.transcript_status, video.transcript_error = "failed", str(exc)
            LOG.error("图文 %s 图片下载失败：%s", video.video_id, exc)
            record_error("download", str(exc))
        store.save_video(video)
        return video.metadata_status == "complete" and video.transcript_status == "images_saved" and not comments_failed
    if not video.media_urls:
        video.transcript_status = "failed"
        video.transcript_error = video.metadata_error or "页面未提供可下载的 HTTPS 媒体地址"
        store.save_video(video)
        LOG.error("视频 %s 下载失败：%s", video.video_id, video.transcript_error)
        record_error("download", video.transcript_error)
        return False
    if not transcribe_media:
        media_path = author_directory(config["download"]["output_dir"], video) / f"{video_stem(video)}.mp4"
        _checkpoint(cancel_requested)
        if on_progress:
            on_progress("download", 60)
        LOG.info("视频 %s 开始下载媒体", video.video_id)
        result = download(video.media_urls, media_path, config["audio"]["download_timeout_seconds"])
        if not result.path:
            LOG.error("视频 %s 下载失败：%s", video.video_id, result.error)
            record_error("download", result.error)
            return False
        video.local_media_path = str(result.path.resolve())
        store.save_video(video)
        LOG.info("视频 %s 下载完成：%s", video.video_id, result.path)
        return video.metadata_status == "complete" and not comments_failed
    media_path = author_directory(config["download"]["output_dir"], video) / f"{video_stem(video)}.mp4"
    _checkpoint(cancel_requested)
    if on_progress:
        on_progress("download", 60)
    LOG.info("视频 %s 开始下载临时媒体", video.video_id)
    result = download(video.media_urls, media_path, config["audio"]["download_timeout_seconds"])
    if not result.path:
        video.transcript_status, video.transcript_error = "failed", result.error
        store.save_video(video)
        LOG.error("视频 %s 下载失败：%s", video.video_id, result.error)
        record_error("download", result.error)
        return False
    LOG.info("视频 %s 已下载临时媒体：%s", video.video_id, result.path)
    video.local_media_path = str(result.path.resolve())
    # Download is an independent selected module: do not let ASR configuration
    # prevent the media file from being fetched and retained.
    store.save_video(video)
    if not config["asr"]["api_key"]:
        video.transcript_status = "failed"
        video.transcript_error = "MiMo API Key 为空；请设置 asr.api_key 或 --mimo-api-key"
        record_error("transcribe", video.transcript_error)
        if not (config["audio"]["keep_temporary_files"] or retain_download):
            result.path.unlink(missing_ok=True)
            video.local_media_path = ""
        store.save_video(video)
        LOG.error("视频 %s 下载完成，但转写前检查失败：%s", video.video_id, video.transcript_error)
        return False
    transcriber = Transcriber(config["audio"], config["asr"])
    try:
        transcriber.check_dependencies()
    except (OSError, ValueError, RuntimeError) as exc:
        video.transcript_status, video.transcript_error = "failed", str(exc)
        record_error("transcribe", str(exc))
        if not (config["audio"]["keep_temporary_files"] or retain_download):
            result.path.unlink(missing_ok=True)
            video.local_media_path = ""
        store.save_video(video)
        LOG.error("视频 %s 下载完成，但转写前检查失败：%s", video.video_id, exc)
        return False
    try:
        if on_progress:
            on_progress("transcribe", 75)
        text, segments = transcriber.transcribe(
            result.path,
            on_segment=lambda parts: (_checkpoint(cancel_requested),
                                      store.save_segments("douyin", video.video_id, parts))[-1],
            existing=store.segments("douyin", video.video_id)
                     if config["crawl"]["resume"] and not config["crawl"]["refresh_completed"] else None)
        video.transcript = text
        failures = [part.error for part in segments if part.status != "complete"]
        video.transcript_status = "partial" if failures and text else "failed" if failures else "complete"
        video.transcript_error = "; ".join(failures)
        if failures:
            record_error("transcribe", video.transcript_error)
            LOG.error("视频 %s 转写%s：%s", video.video_id, video.transcript_status, video.transcript_error)
        else:
            LOG.info("视频 %s 转写完成", video.video_id)
    except TaskCancelled:
        raise
    except Exception as exc:
        video.transcript_status, video.transcript_error = "failed", str(exc)
        record_error("transcribe", str(exc))
        LOG.error("视频 %s 转写失败：%s", video.video_id, exc)
    finally:
        if not (config["audio"]["keep_temporary_files"] or retain_download):
            result.path.unlink(missing_ok=True)
            video.local_media_path = ""
            LOG.info("视频 %s 已删除临时媒体（audio.keep_temporary_files=false）", video.video_id)
        else:
            LOG.info("视频 %s 已保留媒体文件：%s", video.video_id, result.path)
    store.save_video(video)
    return video.transcript_status == "complete" and not comments_failed


def collect_or_run(config: dict, urls: list[str], full: bool, status: dict | None = None,
                   *, include_comments: bool = True,
                   cancel_requested: Callable[[], bool] | None = None,
                   on_progress: Callable[[str, int], None] | None = None,
                   transcribe_media: bool | None = None,
                   retain_download: bool = False,
                   generate_reports: bool | None = None,
                   update_only: bool = False) -> list[Path]:
    if transcribe_media is None:
        transcribe_media = full
    if generate_reports is None:
        generate_reports = full
    store = Store(Path(config["runtime"]["state_db"]))
    adapter = None
    failures = 0
    errors: list[str] = []
    failed_steps: set[str] = set()
    active_video_ids: set[str] = set()
    active_collection_ids: set[str] = set()
    active_profile_urls: set[str] = set()
    try:
        adapter = DouyinAdapter(config["browser"], config["crawl"])
        for input_url in urls:
            _checkpoint(cancel_requested)
            try:
                resolved = input_url
                profile = is_profile(input_url)
                collection = bool(collection_id(input_url))
                if not profile and not collection and not video_id(input_url):
                    resolved, profile = adapter.resolve_kind(input_url)
                    collection = bool(collection_id(resolved))
                    LOG.info("输入链接跳转：%s -> %s", input_url, resolved)
                if collection:
                    identifier = collection_id(resolved)
                    active_collection_ids.add(identifier)
                    previous = store.get_collection("douyin", identifier) if config["crawl"]["resume"] else None
                    discovery = adapter.discover_collection(
                        resolved, previous,
                        on_progress=lambda partial: (_checkpoint(cancel_requested),
                                                     store.save_collection("douyin", partial))[-1])
                    store.save_collection("douyin", discovery)
                    LOG.info("合集发现 %s：%s 条，%s，%s", discovery.url,
                             len(discovery.video_urls), "完整" if discovery.complete else "未确认完整",
                             discovery.stop_reason)
                    limit = config["inputs"]["max_videos_per_collection"]
                    targets = discovery.video_urls if update_only else discovery.video_urls[:limit] if limit is not None else discovery.video_urls
                elif profile:
                    active_profile_urls.add(resolved)
                    previous = store.get_discovery("douyin", resolved) if config["crawl"]["resume"] else None
                    if previous and previous.complete and not config["crawl"]["refresh_completed"] and not update_only:
                        discovery = previous
                    else:
                        discovery = adapter.discover(
                            resolved, None if update_only else config["inputs"]["max_videos_per_profile"], previous,
                            on_progress=lambda partial: (_checkpoint(cancel_requested),
                                                         store.save_discovery("douyin", resolved, partial))[-1])
                    store.save_discovery("douyin", resolved, discovery)
                    LOG.info("主页发现 %s：%s 条，%s，%s", resolved, len(discovery.video_urls),
                             "完整" if discovery.complete else "未确认完整", discovery.stop_reason)
                    targets = discovery.video_urls
                else:
                    targets = [resolved]
                for target in targets:
                    _checkpoint(cancel_requested)
                    identifier = video_id(target)
                    if update_only and identifier and store.get_video("douyin", identifier) is not None:
                        LOG.info("增量更新：视频 %s 已在库，跳过", identifier)
                        continue
                    if identifier:
                        active_video_ids.add(identifier)
                    if not _process_video(adapter, store, target, config, full, include_comments,
                                          cancel_requested, on_progress, transcribe_media,
                                          retain_download, failed_steps, errors):
                        failures += 1
                if (profile or collection) and not targets and not discovery.complete:
                    failures += 1
                    failed_steps.add("collect")
                    errors.append(f"[collect] {discovery.stop_reason}\n输入链接：{input_url}")
            except KeyboardInterrupt:
                raise
            except TaskCancelled:
                raise
            except Exception as exc:
                failures += 1
                failed_steps.add("collect")
                errors.append(f"[collect] {exc}\n输入链接：{input_url}")
                LOG.error("输入处理失败 %s: %s", input_url, exc)
                if is_profile(input_url):
                    from .models import Discovery
                    author_id = urlparse(input_url).path.rstrip("/").split("/")[-1]
                    store.save_discovery("douyin", input_url, Discovery([], False, str(exc), author_id))
                elif collection_id(input_url):
                    from .models import Collection
                    identifier = collection_id(input_url)
                    prior = store.get_collection("douyin", identifier)
                    store.save_collection("douyin", Collection(
                        identifier, f"https://www.douyin.com/collection/{identifier}",
                        prior.name if prior else "", prior.video_urls if prior else [],
                        False, str(exc), prior.video_titles if prior else {}))
        if status is not None:
            status["failed"] = failures
            status["failed_steps"] = sorted(failed_steps)
            status["errors"] = errors
        _checkpoint(cancel_requested)
        if generate_reports and on_progress:
            on_progress("report", 92)
        return (generate(store, config["report"], video_ids=active_video_ids,
                         collection_ids=active_collection_ids,
                         profile_urls=active_profile_urls) if generate_reports else [])
    finally:
        if adapter:
            adapter.close()
        store.close()


def download_one(config: dict, url: str) -> Path:
    adapter = DouyinAdapter(config["browser"], config["crawl"])
    try:
        video = adapter.read_video(url)
        if video.content_type == "image":
            folder = author_directory(config["download"]["output_dir"], video) / video_stem(video)
            return download_images(video.image_urls, folder, config["audio"]["download_timeout_seconds"])
        if not video.media_urls:
            raise RuntimeError(video.metadata_error or "页面未提供可下载的 HTTPS 媒体地址")
        destination = author_directory(config["download"]["output_dir"], video) / f"{video_stem(video)}.mp4"
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
