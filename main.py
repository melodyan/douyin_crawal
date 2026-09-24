"""CLI entry point. Running without arguments executes the full pipeline."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
import yaml

from video_tool.config import DEFAULT_FILE, default_config_path, input_urls, load_config, parser_for, validate
from video_tool.pipeline import collect_or_run, download_one, report_only, transcribe_one


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    defaults = yaml.safe_load(DEFAULT_FILE.read_text(encoding="utf-8"))
    args = parser_for(defaults).parse_args(argv)
    command = args.command or "run"
    project_dir = Path(__file__).resolve().parent
    config_path = args.config or default_config_path(project_dir)
    try:
        config = load_config(config_path, args)
        urls = input_urls(config) if command in {"run", "collect", "download"} else []
        validate(config, command, urls)
        log_path = Path(config["runtime"]["log_file"])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=getattr(logging, config["runtime"]["log_level"]),
                            format="%(asctime)s %(levelname)s %(message)s",
                            handlers=[logging.StreamHandler(), logging.FileHandler(log_path, encoding="utf-8")],
                            force=True)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        status: dict[str, int] = {}
        if command == "run":
            paths = collect_or_run(config, urls, True, status)
        elif command == "collect":
            collect_or_run(config, urls, False, status)
            paths = []
        elif command == "download":
            paths = [download_one(config, urls[0])]
        elif command == "transcribe":
            source = args.input if args.input.is_absolute() else (config_path.resolve().parent / args.input)
            paths = [transcribe_one(config, source.resolve())]
        else:
            paths = report_only(config)
        for path in paths:
            print(path)
        if status.get("failed"):
            print(f"完成，但有 {status['failed']} 个输入或视频处理失败；原因已写入控制台、日志和状态数据库。", file=sys.stderr)
            return 1
        return 0
    except KeyboardInterrupt:
        print("已中断；已完成步骤保存在状态数据库中。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
