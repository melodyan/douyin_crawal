"""FastAPI service used by the Vue workbench."""
from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from video_tool.config import DEFAULT_FILE, FLOAT_MIN, _walk, default_config_path, input_urls, load_config, validate
from video_tool.pipeline import TaskCancelled, collect_or_run, transcribe_one
from video_tool.storage import Store

ROOT = Path(__file__).resolve().parent
DEFAULTS = yaml.safe_load(DEFAULT_FILE.read_text(encoding="utf-8"))
CANCEL_EVENTS: dict[str, threading.Event] = {}
TASK_LOCK = threading.Lock()

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
    if result["transcribe_text"] and not result.get("local_video_path"):
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
        status: dict[str, int] = {}
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
            running = [name for name in relevant if task["steps"].get(name) == "running"]
            task["steps"][running[-1] if running else relevant[-1]] = "failed"
        else:
            for name in relevant:
                task["steps"][name] = "done"
        task["progress"] = 100
        task["state"] = "failed" if status.get("failed") else "done"
        task["error"] = f"{status['failed']} 个输入或作品失败" if status.get("failed") else ""
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


def _create_task(request: TaskRequest) -> dict:
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
    task_id = uuid.uuid4().hex
    task = {
        "id": task_id,
        "title": request.options.get("title") or (Path(str(local_video_path)).name if local_video_path
                                                     else urls[0] if len(urls) == 1 else f"{len(urls)} 个输入"),
        "mode": request.mode,
        "urls": urls,
        "options": request.options,
        "state": "pending",
        "progress": 0,
        "created_at": _now(),
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
    if template is None and value == "":
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
    old = _get_task(task_id)
    options = dict(old.get("options", {}))
    options["force_reprocess"] = True
    return {"data": _create_task(TaskRequest(mode=old["mode"], urls=old.get("urls", []), options=options))}


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


@app.get("/api/videos")
def list_videos(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                author: str = Query("", max_length=100), title: str = Query("", max_length=200),
                published_from: str = Query("", max_length=10), published_to: str = Query("", max_length=10)):
    store = _store()
    try:
        all_items = store.all_videos()
        author_query = author.strip().casefold()
        title_query = title.strip().casefold()
        if author_query:
            all_items = [item for item in all_items
                         if author_query in item.author_name.casefold() or author_query in item.author_id.casefold()]
        if title_query:
            all_items = [item for item in all_items
                         if title_query in item.title.casefold() or title_query in item.description.casefold()]
        if published_from:
            all_items = [item for item in all_items if item.published_at and item.published_at[:10] >= published_from]
        if published_to:
            all_items = [item for item in all_items if item.published_at and item.published_at[:10] <= published_to]
        return {"data": {"items": [v.__dict__ for v in all_items[offset:offset + limit]],
                         "page": offset // limit + 1, "page_size": limit, "total": len(all_items)}}
    finally:
        store.close()


@app.get("/api/videos/{platform}/{video_id}/comments")
def video_comments(platform: str, video_id: str, limit: int = Query(50, ge=1, le=500),
                   offset: int = Query(0, ge=0)):
    store = _store()
    try:
        items = store.comments(platform, video_id)
        return {"data": {"items": [c.__dict__ for c in items[offset:offset + limit]], "total": len(items)}}
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
    items = [{"name": path.name, "size": path.stat().st_size, "updated_at": path.stat().st_mtime}
             for path in sorted(directory.glob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True)] if directory.exists() else []
    return {"data": {"items": items, "total": len(items)}}


@app.get("/api/reports/{name}")
def report_download(name: str):
    directory = Path(_config()["report"]["output_dir"]).resolve()
    path = (directory / name).resolve()
    if path.parent != directory or path.suffix.lower() != ".md" or not path.is_file():
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
