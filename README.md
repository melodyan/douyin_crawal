# 抖音视频和图文采集

Python 3.11+。安装 `pip install -r requirements.txt`，然后运行 `playwright install chromium`，并安装 FFmpeg（使 `ffmpeg` 和 `ffprobe` 可用）。仓库中的 `config.yaml` 是不含密钥和个人链接的配置模板。复制为 `config.local.yaml`，在本地文件中填写链接和 MiMo API Key，再运行 `python main.py`。无参数运行会优先读取 `config.local.yaml`；也可以用 `--config` 指定其他配置文件。

首次运行会打开可见浏览器。遇到登录或验证，请在浏览器中手动完成；访问不到的内容会记录到状态数据库和报告。独立命令：`collect`、`download`、`transcribe`、`report`。用 `python main.py --help` 查看所有配置覆盖选项。

`config.local.yaml` 与浏览器会话可能包含凭证，不应上传；仓库中的 `config.yaml` 保持脱敏。报告在 `outputs/reports`，续跑状态在 `work/state.sqlite3`。采集仅能覆盖当前浏览器会话实际可见的公开视频和一级评论；页面变化或访问限制会降低覆盖率，报告会明确标注。

## 下载与转写排错

- `python main.py download --url 作品链接` 会自动识别视频或图文。视频保存为 `outputs/downloads/<作品ID>.mp4`；图文按页面顺序保存到 `outputs/downloads/<作品ID>/`，只保存图片，不下载配乐。支持直接输入 `/video/<ID>`、`/note/<ID>` 或可跳转到作品的抖音分享链接。
- 完整流程遇到视频会先下载再转写；默认 `audio.keep_temporary_files: false`，所以转写后会删除临时视频。若要保留，设置为 `true`。遇到图文会保存图片并跳过转写，图文任务不需要 MiMo API Key 或 FFmpeg。视频转写仍需 MiMo API Key 和 FFmpeg。
- 报告出现“找不到 FFmpeg/FFprobe”时，安装包含这两个程序的 FFmpeg，把其 `bin` 目录加入 PyCharm 运行配置的 `PATH`，或在 `config.local.yaml` 中把 `audio.ffmpeg_path` 设为 `ffmpeg.exe` 的完整路径。改好后重新运行 `python main.py`，失败的视频会续跑。
- 如果提示“Windows 应用控制策略阻止运行”，说明文件路径正确，但系统禁止执行该版本。请使用设备或组织批准的 FFmpeg 版本，或联系系统管理员检查应用控制策略。
- 下载和转写失败会同时显示在控制台、`runtime.log_file` 和报告中。
