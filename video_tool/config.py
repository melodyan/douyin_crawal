"""Configuration parsing, CLI overrides, and command-specific validation."""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

import yaml


DEFAULT_FILE = Path(__file__).resolve().parent.parent / "config.example.yaml"


def default_config_path(project_dir: Path) -> Path:
    """Use the untracked local config when present, otherwise the safe template."""
    local = project_dir / "config.yaml"
    return local if local.is_file() else project_dir / "config.yaml.bak"


PATH_KEYS = {
    "inputs.urls_file", "browser.profile_dir", "audio.ffmpeg_path",
    "download.output_dir", "asr.output_dir", "report.output_dir",
    "runtime.state_db", "runtime.log_file",
}
INT_MIN = {
    "inputs.max_videos_per_profile": 1, "browser.navigation_timeout_seconds": 1,
    "inputs.max_videos_per_collection": 1,
    "browser.manual_wait_seconds": 0, "crawl.max_retries": 0,
    "crawl.max_comments_per_video": 1, "crawl.no_new_content_scrolls": 1,
    "audio.mp3_bitrate_kbps": 16, "audio.download_timeout_seconds": 1,
    "asr.chunk_seconds": 1, "asr.overlap_seconds": 0, "asr.timeout_seconds": 1,
    "asr.max_retries": 0, "report.top_comments": 1,
    "report.min_comment_chars": 0,
}
FLOAT_MIN = {"crawl.delay_seconds": 0, "crawl.retry_backoff_seconds": 0}
CHOICES = {
    "platform": {"douyin", "xiaohongshu", "bilibili"},
    "asr.language": {"auto", "zh", "en"},
    "runtime.log_level": {"DEBUG", "INFO", "WARNING", "ERROR"},
}
ALIASES = {
    "inputs.max_videos_per_profile": "--inputs-no-video-limit",
    "inputs.max_videos_per_collection": "--inputs-no-collection-limit",
    "crawl.max_comments_per_video": "--crawl-no-comment-limit",
    "report.top_comments": "--report-no-comment-limit",
    "inputs.urls_file": "--no-urls-file",
}


def _walk(mapping: dict, prefix: str = ""):
    for key, value in mapping.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            yield from _walk(value, name)
        else:
            yield name, value


def _set(config: dict, name: str, value):
    keys = name.split(".")
    item = config
    for key in keys[:-1]:
        item = item[key]
    item[keys[-1]] = value


def _get(config: dict, name: str):
    item = config
    for key in name.split("."):
        item = item[key]
    return item


def _add_options(parser: argparse.ArgumentParser, defaults: dict):
    for name, value in _walk(defaults):
        dest = name.replace(".", "_")
        if name == "inputs.urls":
            parser.add_argument("--url", dest=dest, action="append", default=argparse.SUPPRESS)
        elif name == "asr.api_key":
            parser.add_argument("--mimo-api-key", dest=dest, default=argparse.SUPPRESS)
        else:
            flag = "--" + name.replace(".", "-").replace("_", "-")
            # Existing configuration comments use underscores within leaf names.
            leaf_flag = "--" + name.replace(".", "-")
            flags = list(dict.fromkeys((leaf_flag, flag)))
            if name == "inputs.urls_file":
                flags.append("--urls-file")
            if isinstance(value, bool):
                parser.add_argument(*flags, dest=dest, action="store_true", default=argparse.SUPPRESS)
                negative = list(dict.fromkeys(("--no-" + name.replace(".", "-"),
                                                 "--no-" + name.replace(".", "-").replace("_", "-"))))
                parser.add_argument(*negative, dest=dest, action="store_false", default=argparse.SUPPRESS)
            elif value is None and name in ALIASES:
                parser.add_argument(*flags, dest=dest, type=int if name in INT_MIN else str, default=argparse.SUPPRESS)
                parser.add_argument(ALIASES[name], dest=dest, action="store_const", const=None, default=argparse.SUPPRESS)
            else:
                type_ = float if name in FLOAT_MIN else int if name in INT_MIN else str
                parser.add_argument(*flags, dest=dest, type=type_, default=argparse.SUPPRESS)


def parser_for(defaults: dict) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="抖音视频采集、下载、转写和报告")
    parser.add_argument("--config", type=Path)
    _add_options(parser, defaults)
    commands = parser.add_subparsers(dest="command")
    for command in ("run", "collect", "download", "transcribe", "report"):
        sub = commands.add_parser(command)
        sub.add_argument("--config", type=Path, default=argparse.SUPPRESS)
        _add_options(sub, defaults)
        if command == "transcribe":
            sub.add_argument("--input", type=Path, required=True)
    return parser


def load_config(path: Path, overrides: argparse.Namespace | None = None) -> dict:
    path = path.resolve()
    defaults = yaml.safe_load(DEFAULT_FILE.read_text(encoding="utf-8"))
    supplied = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(supplied, dict):
        raise ValueError("配置文件必须是 YAML 对象")
    config = deepcopy(defaults)

    def merge(target, source, template, prefix=""):
        for key, value in source.items():
            name = f"{prefix}.{key}" if prefix else key
            if key not in template:
                raise ValueError(f"未知配置项: {name}")
            if isinstance(template[key], dict):
                if not isinstance(value, dict):
                    raise ValueError(f"配置项必须是对象: {name}")
                merge(target[key], value, template[key], name)
            else:
                target[key] = value

    merge(config, supplied, defaults)
    if overrides:
        values = vars(overrides)
        for name, _ in _walk(defaults):
            dest = name.replace(".", "_")
            if dest in values:
                _set(config, name, values[dest])
    for name in PATH_KEYS:
        value = _get(config, name)
        if value is not None and (name != "audio.ffmpeg_path" or any(c in str(value) for c in ("/", "\\"))):
            candidate = Path(value)
            _set(config, name, candidate if candidate.is_absolute() else (path.parent / candidate).resolve())
    return config


def validate(config: dict, command: str, urls: list[str]):
    defaults = yaml.safe_load(DEFAULT_FILE.read_text(encoding="utf-8"))
    for name, template in _walk(defaults):
        value = _get(config, name)
        if isinstance(template, bool) and not isinstance(value, bool):
            raise ValueError(f"{name} 必须是 true 或 false")
        if isinstance(template, str) and not isinstance(value, (str, Path) if name in PATH_KEYS else str):
            raise ValueError(f"{name} 必须是字符串")
        if isinstance(template, list) and not isinstance(value, list):
            raise ValueError(f"{name} 必须是列表")
        if template is None and value is not None:
            if name in INT_MIN and (isinstance(value, bool) or not isinstance(value, int)):
                raise ValueError(f"{name} 必须是整数或 null")
            if name == "inputs.urls_file" and not isinstance(value, (str, Path)):
                raise ValueError("inputs.urls_file 必须是路径或 null")
    if config["platform"] not in CHOICES["platform"]:
        raise ValueError("platform 必须是 douyin、xiaohongshu 或 bilibili")
    if config["platform"] != "douyin":
        raise ValueError(f"平台 {config['platform']} 暂未支持")
    for name, minimum in INT_MIN.items():
        value = _get(config, name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < minimum):
            raise ValueError(f"{name} 必须是不小于 {minimum} 的整数")
    for name, minimum in FLOAT_MIN.items():
        value = _get(config, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
            raise ValueError(f"{name} 必须不小于 {minimum}")
    for name, choices in CHOICES.items():
        if _get(config, name) not in choices:
            raise ValueError(f"{name} 只能是: {', '.join(sorted(choices))}")
    if config["asr"]["overlap_seconds"] >= config["asr"]["chunk_seconds"]:
        raise ValueError("asr.overlap_seconds 必须小于 asr.chunk_seconds")
    if command in {"run", "collect", "download"} and not urls:
        raise ValueError("没有输入 URL；请在 config.yaml.bak 的 inputs.urls 或 --url 中提供")
    if command == "download" and len(urls) != 1:
        raise ValueError("download 命令只接受一条作品 URL")
    if command == "transcribe" and not config["asr"]["api_key"]:
        raise ValueError("MiMo API Key 为空；请设置 asr.api_key 或 --mimo-api-key")
    for index, url in enumerate(urls, 1):
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not (host == "douyin.com" or host.endswith(".douyin.com") or host == "iesdouyin.com" or host.endswith(".iesdouyin.com")):
            raise ValueError(f"第 {index} 条 URL 不是抖音链接: {url}")


def input_urls(config: dict) -> list[str]:
    raw_urls = config["inputs"]["urls"]
    if not isinstance(raw_urls, list):
        raise ValueError("inputs.urls 必须是 URL 列表")
    urls = list(raw_urls)
    file = config["inputs"]["urls_file"]
    if file:
        urls.extend(line.strip() for line in Path(file).read_text(encoding="utf-8-sig").splitlines() if line.strip() and not line.lstrip().startswith("#"))
    if any(not isinstance(url, str) for url in urls):
        raise ValueError("inputs.urls 只能包含 URL 字符串")
    return list(dict.fromkeys(url.strip() for url in urls if url.strip()))
