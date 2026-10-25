"""Move configured application data and update the local config."""
from __future__ import annotations

import shutil
from pathlib import Path
import yaml

from .naming import author_directory, report_path, video_stem
from .storage import Store


def organize_author_files(config: dict) -> list[tuple[Path, Path]]:
    """Move existing outputs without overwriting files; update saved media paths."""
    media_root = Path(config["download"]["output_dir"]).resolve()
    report_root = Path(config["report"]["output_dir"]).resolve()
    store = Store(Path(config["runtime"]["state_db"]))
    plans = []
    try:
        for video in store.all_videos():
            if video.local_media_path:
                source = Path(video.local_media_path).resolve()
                destination = (author_directory(media_root, video) / source.name).resolve()
                if source.exists() and source.is_relative_to(media_root) and source != destination:
                    plans.append((source, destination, media_root, video))
            source = report_root / f"{video_stem(video)}.md"
            destination = report_path(report_root, video).resolve()
            if source.is_file() and source != destination:
                plans.append((source, destination, report_root, None))
        for source, destination, root, _ in plans:
            if not source.is_relative_to(root) or not destination.is_relative_to(root):
                raise ValueError("文件迁移路径超出配置的输出目录")
            if destination.exists():
                raise FileExistsError(f"目标文件已存在，未覆盖：{destination}")
        moved = []
        for source, destination, _, video in plans:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            if video:
                video.local_media_path = str(destination)
                store.save_video(video)
            moved.append((source, destination))
        return moved
    finally:
        store.close()


def migrate_data(config: dict, target: Path, config_path: Path) -> Path:
    target = target.expanduser().resolve()
    source = Path(config["runtime"]["data_dir"]).resolve()
    target.mkdir(parents=True, exist_ok=True)
    if source != target:
        paths = [Path(config["runtime"]["state_db"]), Path(config["runtime"]["log_file"]),
                 Path(config["browser"]["profile_dir"]), Path(config["download"]["output_dir"]),
                 Path(config["asr"]["output_dir"]), Path(config["report"]["output_dir"])]
        for item in paths:
            if not item.exists():
                continue
            try:
                destination = target / item.relative_to(source)
            except ValueError:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            if item.is_dir():
                shutil.copytree(item, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(item, destination)
    source_config = config_path
    if config_path.name == "config.yaml.bak":
        source_config = config_path.with_name("config.yaml")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    raw.setdefault("runtime", {})["data_dir"] = str(target)
    source_config.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return target
