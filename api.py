"""FastAPI service used by the Vue workbench."""
from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from video_tool.config import DEFAULT_FILE, FLOAT_MIN, _walk, default_config_path, input_urls, load_config, validate
from video_tool.pipeline import TaskCancelled, collect_or_run, transcribe_one
from video_tool.storage import Store
from video_tool.naming import report_path, video_stem
from video_tool.reporter import generate
from video_tool.transcriber import Transcriber
from video_tool.platforms.douyin import DouyinAdapter
from video_tool.pipeline import download_one
from video_tool.platforms.douyin import HASHTAG_RE, collection_id, video_id

ROOT = Path(__file__).resolve().parent
DEFAULTS = yaml.safe_load(DEFAULT_FILE.read_text(encoding="utf-8"))
CANCEL_EVENTS: dict[str, threading.Event] = {}
TASK_LOCK = threading.Lock()
RETRY_LOCK = threading.Lock()

app = FastAPI(title="Douyin Crawler API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class TaskRequest(BaseModel):
    mode: str = Field(default="run", pattern="^(run|collect)$")
    urls: list[str] = Field(default_factory=list)
    options: dict = Field(default_factory=dict)


class ConfigRequest(BaseModel):
    values: dict


class CommentMarkRequest(BaseModel):
    marked: bool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _config_path() -> Path:
    return default_config_path(ROOT)


def _config() -> dict:
    return load_config(_config_path())


def _store(config: dict | None = None) -> Store:
    current = config or _config()
    return Store(Path(current["runtime"]["state_db"]))


def _save_task(task: dict, config: dict | None = None):
    with TASK_LOCK:
        store = _store(config)
        try:
            store.save_task(task)
        finally:
            store.close()


def _get_task(task_id: str, config: dict | None = None) -> dict:
    store = _store(config)
    try:
        task = store.get_task(task_id)
    finally:
        store.close()
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


def _task_options(config: dict, options: dict):
    config["crawl"]["refresh_completed"] = bool(
        options.get("force_reprocess", options.get("refresh_completed", False)))
    for key, section in (("max_comments_per_video", "crawl"),
                         ("max_videos_per_profile", "inputs"),
                         ("max_videos_per_collection", "inputs")):
        if key in options:
            value = options[key]
            config[section][key] = None if value in {None, ""} else int(value)


def _normalized_options(mode: str, options: dict) -> dict:
    result = dict(options)
    result.setdefault("download_video", mode == "run")
    result.setdefault("transcribe_text", mode == "run")
    result.setdefault("include_comments", True)
    result.setdefault("generate_report", mode == "run")
    if result["transcribe_text"] and not result.get("local_video_path") and not result.get("video_action"):
        result["download_video"] = True
    return result


def _run_task(task_id: str, request: TaskRequest, config: dict):
    request.options = _normalized_options(request.mode, request.options)
    task = _get_task(task_id, config)
    event = CANCEL_EVENTS[task_id]
    local_video_path = request.options.get("local_video_path")
    selected = {
        "collect": not bool(local_video_path),
        "comments": bool(request.options["include_comments"]),
        "download": bool(request.options["download_video"]),
        "transcribe": bool(request.options["transcribe_text"]),
        "report": bool(request.options["generate_report"]),
    }

    def progress(step: str, value: int):
        current = _get_task(task_id, config)
        if current["state"] == "cancelled" or event.is_set():
            raise TaskCancelled("任务已取消")
        current["state"] = "running"
        current["progress"] = max(current.get("progress", 0), value)
        order = ["collect", "comments", "download", "transcribe", "report"]
        for previous in order[:order.index(step)]:
            if selected[previous]:
                if current["steps"].get(previous) == "running":
                    current["steps"][previous] = "done"
        current["steps"][step] = "running"
        _save_task(current, config)

    try:
        _task_options(config, request.options)
        if request.options.get("video_action"):
            action = request.options["video_action"]
            progress(action, 10)
            _execute_video_action(config, request.options["video_id"], action)
            task = _get_task(task_id, config)
            task["steps"][action] = "done"
            task.update(state="done", progress=100, error="")
            return
        if local_video_path:
            source = Path(str(local_video_path))
            validate(config, "transcribe", [])
            progress("transcribe", 45)
            output = transcribe_one(config, source)
            task = _get_task(task_id, config)
            task["steps"]["transcribe"] = "done"
            task["progress"] = 100
            task["state"] = "done"
            task["error"] = ""
            task["output_path"] = str(output.resolve())
            return
        urls = request.urls or input_urls(config)
        validate(config, request.mode, urls)
        task["state"] = "running"
        task["progress"] = 5
        task["steps"]["collect"] = "running"
        _save_task(task, config)
        status: dict = {}
        collect_or_run(
            config, urls, selected["download"], status,
            include_comments=selected["comments"],
            cancel_requested=event.is_set,
            on_progress=progress,
            transcribe_media=selected["transcribe"],
            retain_download=selected["download"],
            generate_reports=selected["report"],
        )
        task = _get_task(task_id, config)
        relevant = [name for name in ["collect", "comments", "download", "transcribe", "report"]
                    if selected[name]]
        if status.get("failed"):
            failed_steps = set(status.get("failed_steps", []))
            for name in relevant:
                if name in failed_steps:
                    task["steps"][name] = "failed"
                elif task["steps"].get(name) == "running":
                    task["steps"][name] = "done"
            if not failed_steps:
                running = [name for name in relevant if task["steps"].get(name) == "running"]
                if running:
                    task["steps"][running[-1]] = "failed"
        else:
            for name in relevant:
                task["steps"][name] = "done"
        task["progress"] = 100
        task["state"] = "failed" if status.get("failed") else "done"
        task["error"] = "\n\n".join(status.get("errors", [])) or "任务处理失败，未返回具体错误" if status.get("failed") else ""
    except TaskCancelled:
        task = _get_task(task_id, config)
        task["state"] = "cancelled"
        task["error"] = "任务已取消；已完成的步骤已保存"
        for name, state in task["steps"].items():
            if state == "running":
                task["steps"][name] = "cancelled"
    except Exception as exc:
        task = _get_task(task_id, config)
        task["state"] = "failed"
        task["error"] = str(exc)
        for name, state in task["steps"].items():
            if state == "running":
                task["steps"][name] = "failed"
    finally:
        task["finished_at"] = _now()
        _save_task(task, config)
        CANCEL_EVENTS.pop(task_id, None)


def _create_task(request: TaskRequest, existing: dict | None = None) -> dict:
    config = _config()
    request.options = _normalized_options(request.mode, request.options)
    _task_options(config, request.options)
    local_video_path = request.options.get("local_video_path")
    if local_video_path:
        source = Path(str(local_video_path))
        if not source.is_absolute():
            raise ValueError("本地视频请填写绝对路径")
        source = source.resolve()
        if not source.is_file():
            raise ValueError(f"本地视频不存在或不是文件: {source}")
        if (not request.options.get("transcribe_text") or request.options.get("download_video")
                or request.options.get("include_comments") or request.options.get("generate_report")):
            raise ValueError("本地视频路径仅用于单独执行“转写文字”")
        request.options["local_video_path"] = str(source)
        urls = []
        validate(config, "transcribe", [])
    else:
        if request.urls:
            config["inputs"]["urls"] = request.urls
            config["inputs"]["urls_file"] = None
        urls = input_urls(config)
        validate(config, request.mode, urls)
    request = TaskRequest(mode=request.mode, urls=urls, options=request.options)
    task_id = existing["id"] if existing is not None else uuid.uuid4().hex
    task = {
        "id": task_id,
        "title": (existing["title"] if existing is not None else None) or request.options.get("title") or (Path(str(local_video_path)).name if local_video_path
                                                     else urls[0] if len(urls) == 1 else f"{len(urls)} 个输入"),
        "mode": request.mode,
        "urls": urls,
        "options": request.options,
        "state": "pending",
        "progress": 0,
        "created_at": existing["created_at"] if existing is not None else _now(),
        "finished_at": None,
        "error": "",
        "steps": {"collect": "pending", "comments": "pending", "download": "pending",
                  "transcribe": "pending", "report": "pending"},
    }
    _save_task(task, config)
    CANCEL_EVENTS[task_id] = threading.Event()
    threading.Thread(target=_run_task, args=(task_id, request, config), daemon=True).start()
    return task


def _flatten(values: dict, prefix: str = "") -> dict:
    flat = {}
    for key, value in values.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten(value, name))
        else:
            flat[name] = value
    return flat


def _set_nested(values: dict, name: str, value):
    item = values
    parts = name.split(".")
    for part in parts[:-1]:
        item = item.setdefault(part, {})
    item[parts[-1]] = value


def _coerce_config_value(name: str, value):
    template = dict(_walk(DEFAULTS))[name]
    if name == "inputs.urls" and isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    if template is None and (value is None or value == ""):
        return None
    if isinstance(template, bool):
        if not isinstance(value, bool):
            raise ValueError(f"{name} 必须是 true 或 false")
        return value
    if name in FLOAT_MIN:
        return float(value)
    if isinstance(template, int) or (template is None and name in {
        "inputs.max_videos_per_profile", "inputs.max_videos_per_collection",
        "crawl.max_comments_per_video", "report.top_comments",
    }):
        return None if value == "" else int(value)
    if isinstance(template, float):
        return float(value)
    return value


@app.get("/api/health")
def health():
    config = _config()
    return {"data": {"api": "ok", "sqlite": Path(config["runtime"]["state_db"]).parent.exists()}}


@app.post("/api/tasks", status_code=202)
def create_task(request: TaskRequest):
    try:
        return {"data": _create_task(request)}
    except (OSError, TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/tasks")
def list_tasks():
    store = _store()
    try:
        tasks = store.all_tasks()
        for task in tasks:
            if task["state"] in {"pending", "running"} and task["id"] not in CANCEL_EVENTS:
                task["state"] = "failed"
                task["error"] = "API 服务重启，任务已中断；可点击重试继续"
                task["finished_at"] = _now()
                store.save_task(task)
        return {"data": {"items": tasks, "total": len(tasks)}}
    finally:
        store.close()


@app.get("/api/tasks/events")
async def task_events():
    async def events():
        previous = ""
        while True:
            store = _store()
            try:
                payload = json.dumps(store.all_tasks(), ensure_ascii=False)
            finally:
                store.close()
            if payload != previous:
                yield f"event: tasks\ndata: {payload}\n\n"
                previous = payload
            await asyncio.sleep(1)
    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    return {"data": _get_task(task_id)}


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    task = _get_task(task_id)
    if task["state"] not in {"pending", "running"}:
        raise HTTPException(409, "只有等待中或运行中的任务可以取消")
    event = CANCEL_EVENTS.get(task_id)
    if event:
        event.set()
    task["state"] = "cancelled"
    task["error"] = "正在停止；当前步骤返回后结束"
    _save_task(task)
    return {"data": task}


@app.post("/api/tasks/{task_id}/retry", status_code=202)
def retry_task(task_id: str):
    with RETRY_LOCK:
        old = _get_task(task_id)
        if task_id in CANCEL_EVENTS:
            raise HTTPException(409, "任务仍在处理中，请等待任务停止后重试")
        options = dict(old.get("options", {}))
        options["force_reprocess"] = True
        try:
            return {"data": _create_task(TaskRequest(
                mode=old["mode"], urls=old.get("urls", []), options=options), existing=old)}
        except (OSError, TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str):
    task = _get_task(task_id)
    if task["state"] in {"pending", "running"} or task_id in CANCEL_EVENTS:
        raise HTTPException(409, "请先取消运行中的任务，待任务停止后再删除")
    store = _store()
    try:
        store.delete_task(task_id)
    finally:
        store.close()
    return {"data": {"deleted": True}}


def _video_tags(video) -> list[str]:
    return list(dict.fromkeys(tag.lstrip("#").strip() for tag in
        [*HASHTAG_RE.findall(video.description or ""), *video.hashtags] if tag.lstrip("#").strip()))


@app.get("/api/video-tags")
def video_tags(q: str = Query("", max_length=100)):
    store = _store()
    try:
        counts: dict[str, int] = {}
        for video in store.all_videos():
            for tag in _video_tags(video):
                if q.strip().lstrip("#").casefold() in tag.casefold():
                    counts[tag] = counts.get(tag, 0) + 1
        return {"data": {"items": [{"name": name, "count": counts[name]} for name in sorted(counts)]}}
    finally:
        store.close()


@app.get("/api/video-authors")
def video_authors(q: str = Query("", max_length=100)):
    store = _store()
    try:
        counts: dict[str, int] = {}
        for video in store.all_videos():
            name = video.author_name or "未知作者"
            if q.strip().casefold() in name.casefold():
                counts[name] = counts.get(name, 0) + 1
        return {"data": {"items": [{"name": name, "count": counts[name]} for name in sorted(counts)]}}
    finally:
        store.close()


@app.get("/api/videos")
def list_videos(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                author: str = Query("", max_length=100), title: str = Query("", max_length=200),
                published_from: str = Query("", max_length=10), published_to: str = Query("", max_length=10),
                source_url: str = Query("", max_length=500),
                author_names: list[str] = Query(default=[]), tags: list[str] = Query(default=[])):
    store = _store()
    try:
        all_items = store.all_videos()
        current_config = _config()
        active = store.all_tasks()
        def video_payload(video):
            payload = dict(video.__dict__)
            payload["hashtags"] = _video_tags(video)
            payload["download_complete"] = bool(video.local_media_path and Path(video.local_media_path).exists())
            payload["report_complete"] = report_path(current_config["report"]["output_dir"], video).is_file()
            payload["running_actions"] = [t["options"]["video_action"] for t in active
                if t["state"] in {"pending", "running"} and t.get("options", {}).get("video_id") == video.video_id
                and t.get("options", {}).get("video_action")]
            return payload
        author_query = author.strip().casefold()
        title_query = title.strip().casefold()
        if author_query:
            all_items = [item for item in all_items
                         if author_query in item.author_name.casefold()]
        if author_names:
            selected_names = set(author_names)
            all_items = [item for item in all_items if (item.author_name or "未知作者") in selected_names]
        if tags:
            selected_tags = {tag.strip().lstrip("#") for tag in tags if tag.strip().lstrip("#")}
            if selected_tags:
                all_items = [item for item in all_items if selected_tags.intersection(_video_tags(item))]
        if title_query:
            all_items = [item for item in all_items
                         if title_query in item.title.casefold() or title_query in item.description.casefold()]
        if published_from:
            all_items = [item for item in all_items if item.published_at and item.published_at[:10] >= published_from]
        if published_to:
            all_items = [item for item in all_items if item.published_at and item.published_at[:10] <= published_to]
        source = source_url.strip()
        if source:
            source_ids: set[str] = set()
            identifier = video_id(source)
            if identifier:
                source_ids.add(identifier)
            collection = collection_id(source)
            if collection:
                saved = store.get_collection("douyin", collection)
                if saved:
                    source_ids.update(video_id(url) for url in saved.video_urls if video_id(url))
            if "/user/" in source:
                for row in store.all_discoveries():
                    if row["profile_url"].rstrip("/") == source.rstrip("/"):
                        source_ids.update(video_id(url) for url in json.loads(row["video_urls"]) if video_id(url))
            if source_ids:
                all_items = [item for item in all_items if item.video_id in source_ids]
            else:
                all_items = []
        return {"data": {"items": [video_payload(v) for v in all_items[offset:offset + limit]],
                         "page": offset // limit + 1, "page_size": limit, "total": len(all_items)}}
    finally:
        store.close()


def _execute_video_action(config: dict, identifier: str, action: str):
    store = _store(config)
    try:
        video = store.get_video("douyin", identifier)
        if not video:
            raise ValueError("视频记录不存在")
        if action == "download":
            video.local_media_path = str(download_one(config, video.url).resolve())
            store.save_video(video)
        elif action == "comments":
            adapter = DouyinAdapter(config["browser"], config["crawl"])
            try:
                result = adapter.read_comments(video, config["crawl"]["max_comments_per_video"])
                store.save_comments(video.platform, identifier, result, replace=True)
                if not result.complete:
                    raise RuntimeError(result.stop_reason or "评论采集未完成")
            finally:
                adapter.close()
        elif action == "transcribe":
            if not video.local_media_path or not Path(video.local_media_path).is_file():
                raise ValueError("请先下载视频，再执行转写")
            validate(config, "transcribe", [])
            transcriber = Transcriber(config["audio"], config["asr"])
            try:
                text, segments = transcriber.transcribe(Path(video.local_media_path))
                store.save_segments(video.platform, identifier, segments)
                video.transcript = text
                errors = [part.error for part in segments if part.status != "complete"]
                video.transcript_status = "failed" if errors else "complete"
                video.transcript_error = "; ".join(errors)
                store.save_video(video)
                if errors:
                    raise RuntimeError(video.transcript_error)
            except Exception as exc:
                video.transcript_status, video.transcript_error = "failed", str(exc)
                store.save_video(video)
                raise
        elif action == "report":
            paths = generate(store, config["report"], video_ids={identifier}, collection_ids=set(), profile_urls=set())
            if not paths:
                raise ValueError("报告未生成，请检查报告配置及视频数据是否完整")
    finally:
        store.close()


@app.post("/api/videos/{platform}/{identifier}/actions/{action}", status_code=202)
def start_video_action(platform: str, identifier: str, action: str):
    if platform != "douyin" or action not in {"download", "comments", "transcribe", "report"}:
        raise HTTPException(422, "不支持的流程")
    with RETRY_LOCK:
        config = _config()
        store = _store(config)
        try:
            video = store.get_video(platform, identifier)
            if not video:
                raise HTTPException(404, "视频记录不存在")
            for task in store.all_tasks():
                if task["id"] in CANCEL_EVENTS and (task.get("options", {}).get("video_id") == identifier
                        or any(video_id(url) == identifier for url in task.get("urls", []))):
                    raise HTTPException(409, "该视频仍有流程运行，请稍后再试")
            if action == "transcribe" and video.content_type == "image":
                raise HTTPException(422, "图文作品不支持语音转写")
            if action == "transcribe" and (not video.local_media_path or not Path(video.local_media_path).is_file()):
                raise HTTPException(422, "请先下载视频，再执行转写")
            options = dict(video_action=action, video_id=identifier, title=f"{video.title or identifier} · {action}",
                           download_video=False, transcribe_text=False, include_comments=False, generate_report=False)
            options[{"download": "download_video", "comments": "include_comments", "transcribe": "transcribe_text", "report": "generate_report"}[action]] = True
            return {"data": _create_task(TaskRequest(mode="collect", urls=[video.url], options=options))}
        except (OSError, TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            store.close()


@app.delete("/api/videos/{platform}/{video_id}")
def delete_video(platform: str, video_id: str):
    store = _store()
    try:
        store.delete_video(platform, video_id)
        return {"data": {"deleted": True}}
    finally:
        store.close()


@app.post("/api/videos/{platform}/{video_id}/report/reveal")
def reveal_video_report(platform: str, video_id: str):
    config = _config()
    store = _store(config)
    try:
        video = store.get_video(platform, video_id)
        if not video:
            raise HTTPException(404, "视频记录不存在")
        directory = Path(config["report"]["output_dir"]).resolve()
        path = report_path(directory, video).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            raise HTTPException(404, "报告文件不存在，请重新生成报告")
        try:
            # Explorer needs the filename quoted separately from /select,.
            # Quoting the entire switch and filename can open its default folder.
            subprocess.Popen(f'explorer.exe /select,"{path}"')
        except OSError as exc:
            raise HTTPException(500, f"无法打开报告所在目录：{exc}") from exc
        return {"data": {"path": str(path)}}
    finally:
        store.close()


@app.get("/api/videos/{platform}/{video_id}/media")
def video_media(platform: str, video_id: str):
    store = _store()
    try:
        video = store.get_video(platform, video_id)
        if not video or not video.local_media_path or video.content_type == "image":
            raise HTTPException(404, "暂无已下载的视频，请先下载视频")
        path = Path(video.local_media_path)
        if not path.is_file():
            raise HTTPException(404, "本地视频文件不存在，请重新下载")
        return FileResponse(path, media_type="video/mp4", content_disposition_type="inline")
    finally:
        store.close()


@app.get("/api/videos/{platform}/{video_id}/comments")
def video_comments(platform: str, video_id: str, limit: int = Query(50, ge=1, le=500),
                   offset: int = Query(0, ge=0), marked_only: bool = False):
    store = _store()
    try:
        items = store.comments(platform, video_id)
        marks = store.marked_comments(platform, video_id)
        if marked_only:
            items = [c for c in items if c.comment_id in marks]
        return {"data": {"items": [dict(c.__dict__, marked=c.comment_id in marks)
                                    for c in items[offset:offset + limit]], "total": len(items)}}
    finally:
        store.close()


@app.put("/api/videos/{platform}/{video_id}/comments/{comment_id}/mark")
def mark_video_comment(platform: str, video_id: str, comment_id: str, request: CommentMarkRequest):
    store = _store()
    try:
        store.mark_comment(platform, video_id, comment_id, request.marked)
        return {"data": {"marked": request.marked}}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    finally:
        store.close()


@app.get("/api/videos/{platform}/{video_id}/segments")
def video_segments(platform: str, video_id: str):
    store = _store()
    try:
        return {"data": {"items": [s.__dict__ for s in store.segments(platform, video_id)]}}
    finally:
        store.close()


@app.get("/api/discoveries")
def discoveries():
    store = _store()
    try:
        return {"data": {"items": [dict(row) for row in store.all_discoveries()]}}
    finally:
        store.close()


@app.get("/api/collections")
def collections():
    store = _store()
    try:
        return {"data": {"items": [item.__dict__ for item in store.all_collections()]}}
    finally:
        store.close()


@app.get("/api/reports")
def reports():
    directory = Path(_config()["report"]["output_dir"])
    items = [{"name": path.relative_to(directory).as_posix(), "size": path.stat().st_size, "updated_at": path.stat().st_mtime}
             for path in sorted(directory.rglob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True)] if directory.exists() else []
    return {"data": {"items": items, "total": len(items)}}


@app.get("/api/reports/{name:path}")
def report_download(name: str):
    directory = Path(_config()["report"]["output_dir"]).resolve()
    path = (directory / name).resolve()
    if not path.is_relative_to(directory) or path.suffix.lower() != ".md" or not path.is_file():
        raise HTTPException(404, "报告不存在")
    return FileResponse(path, media_type="text/markdown; charset=utf-8", filename=path.name)


@app.get("/api/config")
def get_config():
    raw = yaml.safe_load(_config_path().read_text(encoding="utf-8")) or {}
    merged = deepcopy(DEFAULTS)
    for name, value in _flatten(raw).items():
        _set_nested(merged, name, value)
    flat = _flatten(merged)
    secret_set = bool(flat.pop("asr.api_key", ""))
    if isinstance(flat.get("inputs.urls"), list):
        flat["inputs.urls"] = "\n".join(flat["inputs.urls"])
    return {"data": {"values": flat, "secrets": {"asr.api_key": secret_set}}}


@app.get("/api/config/api-key")
def get_api_key():
    raw = yaml.safe_load(_config_path().read_text(encoding="utf-8")) or {}
    return JSONResponse(
        {"data": {"value": raw.get("asr", {}).get("api_key") or ""}},
        headers={"Cache-Control": "no-store"},
    )


@app.put("/api/config")
def save_config(request: ConfigRequest):
    allowed = {name for name, _ in _walk(DEFAULTS)}
    unknown = sorted(set(request.values) - allowed)
    if unknown:
        raise HTTPException(422, f"未知配置项: {', '.join(unknown)}")
    source_path = _config_path()
    raw = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {}
    try:
        for name, value in request.values.items():
            if name == "asr.api_key" and value == "":
                continue
            _set_nested(raw, name, _coerce_config_value(name, value))
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", dir=ROOT, encoding="utf-8", delete=False) as handle:
            temp_path = Path(handle.name)
            yaml.safe_dump(raw, handle, allow_unicode=True, sort_keys=False)
        try:
            candidate = load_config(temp_path)
            validate(candidate, "report", [])
        finally:
            temp_path.unlink(missing_ok=True)
    except (OSError, TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    write_path = ROOT / "config.yaml"
    write_path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"data": {"saved": True, "path": str(write_path)}}
