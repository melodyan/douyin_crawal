"""Move configured application data and update the local config."""
from __future__ import annotations

import shutil
from pathlib import Path
import yaml


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
