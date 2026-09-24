"""Generate Markdown exclusively from stored normalized records."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .models import Video
from .storage import Store


def escape(text: str) -> str:
    text = str(text or "").replace("\r", " ").replace("\n", " ")
    text = text.replace("&", "&amp;").replace("<", "&lt;")
    text = text.replace("\\", "\\\\")
    text = re.sub(r"([`*_{}\[\]()#+\-.!|>])", r"\\\1", text)
    return text


def safe_filename(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", value).strip(" .")[:100] or "unknown"


def _quote(text: str) -> str:
    return "\n".join("> " + escape(line) for line in (text or "").splitlines()) or "> （空）"


def generate(store: Store, config: dict) -> list[Path]:
    output = Path(config["output_dir"])
    output.mkdir(parents=True, exist_ok=True)
    groups: dict[tuple[str, str], list[Video]] = {}
    for video in store.all_videos():
        key = (video.platform, video.author_id or f"video_{video.video_id}")
        groups.setdefault(key, []).append(video)
    for discovery in store.all_discoveries():
        if discovery["author_id"]:
            groups.setdefault((discovery["platform"], discovery["author_id"]), [])
    paths = []
    for (platform, author_id), videos in groups.items():
        author_name = next((video.author_name for video in videos if video.author_name), "未知博主")
        discoveries = store.discoveries(platform, author_id)
        if author_name == "未知博主":
            author_name = next((row["author_name"] for row in discoveries if row["author_name"]), "未知博主")
        label = author_name if not author_id.startswith("video_") else videos[0].video_id
        filename = f"{safe_filename(platform)}_{safe_filename(label)}_{safe_filename(author_id)}.md"
        path = output / filename
        lines = [f"# {escape(platform)} · {escape(author_name)}", "",
                 f"生成时间：{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
                 f"视频条目：{len(videos)}", ""]
        if discoveries:
            lines.append("## 主页采集覆盖情况")
            lines.append("")
            for discovery in discoveries:
                status = "确认完整" if discovery["complete"] else "未确认完整"
                lines.append(f"- {escape(discovery['profile_url'])}：{status}，发现 {discovery['video_count']} 条；{escape(discovery['stop_reason'])}")
        else:
            lines.append("主页覆盖情况：未执行主页发现，或无法关联到已采集博主。")
        lines.append("")
        omitted = []
        for video in videos:
            if not config["write_partial_results"] and (not video.title or not video.url or video.transcript_status != "complete"):
                omitted.append(video)
                continue
            lines.extend([f"## {escape(video.title or '标题未读取')} · {escape(video.video_id)}", "",
                          f"- 视频链接：{video.url}",
                          f"- 标题状态：{'已读取' if video.title else '无法读取'}",
                          f"- 元数据状态：{escape(video.metadata_status)}{('；' + escape(video.metadata_error)) if video.metadata_error else ''}"])
            if video.tags_status == "present":
                lines.append("- 原有话题：" + " ".join("\\#" + escape(tag) for tag in video.hashtags))
            elif video.tags_status == "absent":
                lines.append("- 原有话题：页面未显示话题")
            else:
                lines.append("- 原有话题：无法读取")
            lines.extend([f"- 转写状态：{escape(video.transcript_status)}{('；' + escape(video.transcript_error)) if video.transcript_error else ''}",
                          "", "### 转写全文", "", _quote(video.transcript) if video.transcript else
                          ("> 尚未运行转写" if video.transcript_status == "pending" else "> 转写不可得"), ""])
            comments = store.comments(platform, video.video_id)
            label = "点赞最高的" if video.comments_complete else "已采集评论的点赞前"
            top = comments[:config["top_comments"]]
            lines.extend([f"### {label} {len(top)} 条一级评论", "",
                          f"采集数量：{len(comments)}；覆盖：{'确认完整' if video.comments_complete else '未确认完整'}；停止原因：{escape(video.comments_stop_reason)}", ""])
            if not top:
                lines.extend(["（没有采集到可用一级评论）", ""])
            for index, comment in enumerate(top, 1):
                by = f" · {escape(comment.author)}" if config["include_comment_author"] and comment.author else ""
                lines.extend([f"{index}. 赞 {comment.likes}{by}", "", _quote(comment.text), ""])
        if omitted:
            lines.extend(["## 未写入正文的视频", ""])
            for video in omitted:
                lines.append(f"- {escape(video.video_id)}：{escape(video.transcript_error or video.metadata_error or '标题、链接或转写未完成')}")
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        paths.append(path)
    return paths
