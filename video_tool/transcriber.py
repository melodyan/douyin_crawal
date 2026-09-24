"""FFmpeg chunking and MiMo ASR transport."""
from __future__ import annotations

import base64
import math
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

import httpx

from .models import SegmentResult

MAX_BASE64_BYTES = 10 * 1024 * 1024


def merge_text(previous: str, current: str) -> str:
    if not previous:
        return current
    if not current:
        return previous
    left, right = previous.rstrip(), current.lstrip()
    for size in range(min(len(left), len(right), 160), 1, -1):
        if left[-size:] == right[:size]:
            return left + right[size:]
    return left + ("" if right[:1] in "，。！？,.!?" else "\n") + right


class Transcriber:
    def __init__(self, audio: dict, asr: dict):
        self.audio = audio
        self.asr = asr
        self._tools: tuple[str, str] | None = None

    def check_dependencies(self) -> tuple[str, str]:
        """Resolve and launch both FFmpeg executables before downloading media."""
        if self._tools is not None:
            return self._tools
        configured = str(self.audio["ffmpeg_path"])
        configured_path = Path(configured)
        if configured_path.is_dir():
            configured = str(configured_path / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg"))
        ffmpeg = shutil.which(configured)
        if not ffmpeg:
            raise FileNotFoundError(
                f"找不到 FFmpeg：{configured}。请安装包含 ffprobe 的 FFmpeg，并将其 bin 目录加入 PyCharm 运行环境的 PATH；"
                "或将 config.yaml.bak 的 audio.ffmpeg_path 设置为 ffmpeg.exe 的完整路径。"
            )
        executable = Path(ffmpeg)
        sibling = executable.with_name("ffprobe.exe" if executable.suffix.lower() == ".exe" else "ffprobe")
        ffprobe = str(sibling) if sibling.is_file() else shutil.which("ffprobe")
        if not ffprobe:
            raise FileNotFoundError(
                f"已找到 FFmpeg，但找不到 FFprobe（应与 {executable.name} 位于同一目录）。"
                "请安装完整 FFmpeg，或将 ffprobe 所在目录加入 PyCharm 运行环境的 PATH。"
            )
        for label, executable_path in (("FFmpeg", ffmpeg), ("FFprobe", ffprobe)):
            try:
                result = subprocess.run([executable_path, "-version"], stdout=subprocess.DEVNULL,
                                        stderr=subprocess.PIPE, text=True, timeout=20, check=False)
            except OSError as exc:
                if getattr(exc, "winerror", None) == 4551 or "应用程序控制策略" in str(exc):
                    raise RuntimeError(
                        f"{label} 文件已找到，但 Windows 应用控制策略阻止运行。"
                        "请使用设备或组织允许的 FFmpeg 版本，或联系系统管理员检查应用控制策略。"
                    ) from exc
                raise RuntimeError(f"{label} 文件已找到，但无法启动：{exc}") from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"{label} 文件已找到，但启动检查超时：{executable_path}") from exc
            if result.returncode != 0:
                raise RuntimeError(
                    f"{label} 文件已找到，但启动失败（退出码 {result.returncode}）：{result.stderr[-300:]}"
                )
        self._tools = (ffmpeg, ffprobe)
        return self._tools

    def _probe(self, source: Path) -> float:
        _, ffprobe = self.check_dependencies()
        result = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                                 "-of", "default=noprint_wrappers=1:nokey=1", str(source)],
                                capture_output=True, text=True, timeout=30, check=True)
        duration = float(result.stdout.strip())
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("无法读取有效媒体时长")
        return duration

    def _extract(self, source: Path, path: Path, start: float, end: float):
        ffmpeg, _ = self.check_dependencies()
        result = subprocess.run([ffmpeg, "-nostdin", "-y", "-loglevel", "error",
                                 "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{end-start:.3f}",
                                 "-vn", "-ac", "1", "-b:a", f"{self.audio['mp3_bitrate_kbps']}k", str(path)],
                                capture_output=True, text=True, timeout=max(60, int((end-start)*3)), check=False)
        if result.returncode or not path.exists() or path.stat().st_size == 0:
            raise RuntimeError(f"FFmpeg 提取失败: {result.stderr[-400:]}")

    def _request(self, encoded: str) -> str:
        body = {"model": self.asr["model"], "messages": [{"role": "user", "content": [
            {"type": "input_audio", "input_audio": {"data": "data:audio/mpeg;base64," + encoded}}]}],
            "asr_options": {"language": self.asr["language"]}}
        endpoint = self.asr["base_url"].rstrip("/") + "/chat/completions"
        for attempt in range(self.asr["max_retries"] + 1):
            try:
                response = httpx.post(endpoint, json=body,
                                      headers={"api-key": self.asr["api_key"]},
                                      timeout=self.asr["timeout_seconds"])
                if response.status_code in (429, 500, 502, 503, 504) and attempt < self.asr["max_retries"]:
                    time.sleep(min(30, 2 ** attempt))
                    continue
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                if not isinstance(content, str):
                    raise ValueError("MiMo 返回了非文本结果")
                return content.strip()
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == self.asr["max_retries"]:
                    raise
                time.sleep(min(30, 2 ** attempt))
        raise RuntimeError("MiMo 请求重试次数耗尽")

    def transcribe(self, source: Path, on_segment: Callable[[list[SegmentResult]], None] | None = None,
                   existing: list[SegmentResult] | None = None) -> tuple[str, list[SegmentResult]]:
        if not source.is_file():
            raise FileNotFoundError(source)
        duration = self._probe(source)
        step = self.asr["chunk_seconds"] - self.asr["overlap_seconds"]
        starts = []
        start = 0.0
        while start < duration:
            finish = min(duration, start + self.asr["chunk_seconds"])
            starts.append((start, finish))
            if finish >= duration:
                break
            start += step
        saved = {(round(item.start_seconds, 3), round(item.end_seconds, 3)): item
                 for item in existing or [] if item.status == "complete"}
        segments: list[SegmentResult] = []
        with tempfile.TemporaryDirectory(prefix="mimo_asr_") as temp_dir:
            def process(begin: float, finish: float):
                key = (round(begin, 3), round(finish, 3))
                if key in saved:
                    segments.append(saved[key])
                    return
                path = Path(temp_dir) / f"segment_{len(segments)}_{int(begin*1000)}.mp3"
                try:
                    self._extract(source, path, begin, finish)
                    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                    if len(encoded) > MAX_BASE64_BYTES:
                        if finish - begin < 2:
                            raise ValueError("最短片段仍超过 MiMo 10 MB 限制")
                        midpoint = (begin + finish) / 2
                        process(begin, midpoint)
                        process(midpoint, finish)
                        return
                    part = SegmentResult(len(segments), begin, finish, "complete", self._request(encoded))
                except Exception as exc:
                    part = SegmentResult(len(segments), begin, finish, "failed", error=str(exc))
                finally:
                    path.unlink(missing_ok=True)
                segments.append(part)
                if on_segment:
                    on_segment(segments)

            for begin, finish in starts:
                process(begin, finish)
        segments.sort(key=lambda item: (item.start_seconds, item.end_seconds))
        for index, part in enumerate(segments):
            part.index = index
        if on_segment:
            on_segment(segments)
        transcript = ""
        for part in segments:
            if part.status == "complete":
                transcript = merge_text(transcript, part.text)
        return transcript, segments
