# 抖音内容采集、下载与转写工具

从抖音作品、博主主页或合集链接采集公开作品信息和评论；下载视频或图文，并将视频转写为文本报告。

## 功能概览

- 支持作品直链、`v.douyin.com` 短链、博主主页和合集链接。
- 可直接粘贴完整的抖音分享文案；程序会自动提取其中的第一个抖音链接。
- 采集作品信息、一级评论及其回复，并将进度保存在 SQLite 状态库中。
- 视频可下载并调用 MiMo ASR 转写；图文会按顺序下载全部图片。
- 默认续跑，已完成的下载和转写会跳过；支持针对单条作品强制重跑。
- 为每条作品生成 Markdown 报告，并可生成博主、合集目录报告。

## 安装

要求 Python 3.11 或更高版本。建议在项目目录中创建虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

视频转写还需要：

- 已安装 FFmpeg，且 `ffmpeg` 与 `ffprobe` 可从命令行调用；或在配置中设置 `audio.ffmpeg_path`。
- MiMo API Key，写入 `asr.api_key` 或运行时传入 `--mimo-api-key`。

首次运行时，程序会创建或复用 `browser.profile_dir` 中的浏览器会话。遇到登录、验证码或安全验证时，请在打开的浏览器中完成操作。

## 快速开始

1. 复制示例配置：

   ```powershell
   Copy-Item config.example.yaml config.yaml
   ```

2. 在 `config.yaml` 填写 `inputs.urls`，并在需要视频转写时填写 `asr.api_key`。

3. 运行完整流程：

   ```powershell
   python main.py
   ```

程序优先读取项目根目录的 `config.yaml`；没有该文件时读取 `config.yaml.bak`。也可指定其他配置文件：

```powershell
python main.py --config .\my-config.yaml
```

查看完整命令行选项：

```powershell
python main.py --help
python main.py run --help
```

## 输入链接

### 在配置文件中输入

`inputs.urls` 的每一项可以是作品链接、主页链接、合集链接，或完整的抖音分享文案：

```yaml
inputs:
  urls:
    - 2.35 :7pm Q@X.MJ wsr:/ 零基础搭 AI 知识库 # AI学习 https://v.douyin.com/mnk7O2rjeEE/ 复制此链接，打开抖音搜索
    - https://www.douyin.com/video/1234567890123456789
```

即使分享文案中包含 `#` 话题、未使用引号，程序也会从 `inputs.urls` 的原始文本中提取抖音链接。一个输入只使用其中的第一条抖音链接。

支持的输入类型：

| 类型 | 示例 | 行为 |
| --- | --- | --- |
| 单条作品 | `https://www.douyin.com/video/<ID>` | 处理这一条视频或图文 |
| 短链 | `https://v.douyin.com/<code>/` | 浏览器跳转后处理对应作品 |
| 博主主页 | `https://www.douyin.com/user/<ID>` | 发现并处理主页作品 |
| 合集 | `https://www.douyin.com/collection/<ID>/1` | 按合集顺序发现并处理作品 |

### 从文本文件批量输入

将一条链接或一段分享文案放在一行，保存为 UTF-8 文本文件，例如 `work/urls.txt`：

```text
2.35 :7pm ... # AI学习 https://v.douyin.com/mnk7O2rjeEE/ 复制此链接
https://www.douyin.com/video/1234567890123456789
```

然后配置：

```yaml
inputs:
  urls_file: "work/urls.txt"
```

也可以临时传入：

```powershell
python main.py run --urls-file .\work\urls.txt
```

空行和以 `#` 开头的行会被忽略，重复链接会去重。

### 命令行临时输入

`--url` 可重复传入，每次运行时会覆盖配置文件中的 `inputs.urls`：

```powershell
python main.py run --url "https://v.douyin.com/mnk7O2rjeEE/"
python main.py run --url "分享文案 1" --url "分享文案 2"
```

## 命令说明

### `run`：完整流程

默认命令。依次采集作品信息和评论、下载媒体、转写视频，并生成报告。

```powershell
python main.py
python main.py run --url "抖音分享文案或链接"
```

视频完整流程需要 MiMo API Key 和 FFmpeg。图文只下载图片，不需要 MiMo API Key 或 FFmpeg。

### `collect`：只采集，不下载和转写

采集作品信息和评论，保存至状态库；不下载媒体、不调用转写、也不生成报告。

```powershell
python main.py collect --url "https://v.douyin.com/mnk7O2rjeEE/"
```

### `download`：只下载单条作品

只下载一条视频或图文，不写入状态库、不采评论、不转写。该命令只能接收一条输入。

```powershell
python main.py download --url "https://v.douyin.com/mnk7O2rjeEE/"
```

视频保存为 `outputs/downloads/<标题>_<作品ID>.mp4`；图文保存为 `outputs/downloads/<标题>_<作品ID>/`，按 `01.jpg`、`02.png` 等顺序命名。

### `transcribe`：转写本地媒体文件

转写本地视频或音频，不访问抖音。需要 MiMo API Key 和 FFmpeg。

```powershell
python main.py transcribe --input .\outputs\downloads\video.mp4
```

结果写入 `asr.output_dir`，默认是 `outputs/transcripts`。

### `report`：重新生成历史报告

从状态库中已有的数据重新生成全部历史报告，不访问抖音，也不下载或转写：

```powershell
python main.py report
```

与之相对，`run` 只生成本次输入对应的报告，已存在的其它报告不会被删除。

## 续跑与强制重跑

### 默认续跑

`crawl.resume: true` 是默认设置。状态库中已有成功转写的作品会跳过下载和转写；若评论尚未采集完整，程序仍会继续采评论。日志会说明跳过原因。

完整流程默认会删除转写用的临时视频：

```yaml
audio:
  keep_temporary_files: false
```

这不会影响续跑判断，是否已经成功处理以状态库中的转写结果为准。设为 `true` 会保留视频文件，状态检查日志也会提示文件是否仍存在。

### 强制重跑单条作品

使用 `--force-reprocess` 强制重新采集、下载和转写当前输入，覆盖该作品的评论、转写结果和报告：

```powershell
python main.py run --force-reprocess --url "https://v.douyin.com/mnk7O2rjeEE/"
```

### 强制重跑当前全部输入

在配置中设置：

```yaml
crawl:
  refresh_completed: true
```

或运行时使用等效参数：

```powershell
python main.py run --crawl-refresh-completed
```

强制重跑会增加下载量与 MiMo 调用量；完成后可将该配置恢复为 `false`。

## 常用配置

所有 CLI 参数都可以覆盖同名配置项。配置名中的 `.` 通常改为 `-`，例如 `report.output_dir` 对应 `--report-output-dir`。

| 配置项 | 默认值 | 用途 |
| --- | --- | --- |
| `inputs.urls` | `[]` | 链接或分享文案列表 |
| `inputs.urls_file` | `null` | 批量链接文本文件 |
| `inputs.max_videos_per_profile` | `null` | 单次处理博主主页作品上限 |
| `inputs.max_videos_per_collection` | `null` | 单次处理合集作品上限 |
| `browser.headless` | `false` | `false` 显示浏览器，便于登录和验证码操作 |
| `browser.manual_wait_seconds` | `0` | 验证时等待秒数，`0` 表示持续等待 |
| `crawl.max_comments_per_video` | `null` | 每条视频采集的一级评论上限 |
| `crawl.resume` | `true` | 复用状态库中的进度与结果 |
| `crawl.refresh_completed` | `false` | 强制重跑已完成作品 |
| `audio.keep_temporary_files` | `false` | 转写后是否保留视频文件 |
| `audio.ffmpeg_path` | `ffmpeg` | FFmpeg 可执行文件或目录 |
| `asr.api_key` | 空 | MiMo API Key |
| `report.min_comment_chars` | `50` | 写入报告的最小评论字数；`0` 关闭过滤 |
| `report.top_comments` | `null` | 每份报告保留的高赞一级评论数；`null` 表示全部 |
| `runtime.log_file` | `work/run.log` | 运行日志位置 |
| `runtime.state_db` | `work/state.sqlite3` | 续跑状态库位置 |

常用参数示例：

```powershell
# 可见浏览器运行，适合首次登录
python main.py run --no-browser-headless

# 只处理主页前 10 条作品
python main.py run --url "博主主页链接" --inputs-max-videos-per-profile 10

# 只处理合集前 2 条作品
python main.py run --url "合集链接" --inputs-max-videos-per-collection 2

# 限制每条视频最多采集 100 条一级评论
python main.py collect --url "作品链接" --crawl-max-comments-per-video 100

# 运行时提供 API Key
python main.py run --url "作品链接" --mimo-api-key "你的 Key"
```

## 输出文件

| 位置 | 内容 |
| --- | --- |
| `outputs/downloads/` | `download` 命令下载的视频或图文；完整流程仅在保留临时文件时保留视频 |
| `outputs/transcripts/` | `transcribe` 命令的本地转写文本 |
| `outputs/reports/` | 单作品、博主主页和合集 Markdown 报告 |
| `work/state.sqlite3` | 作品、评论、转写分段和续跑状态 |
| `work/run.log` | 控制台同样输出的运行日志 |
| `work/browser/` | 浏览器登录会话 |

## 常见问题

### 浏览器要求登录、验证码或安全验证

将 `browser.headless` 设为 `false`，或加 `--no-browser-headless`，在打开的浏览器中完成登录或验证。浏览器会话会保存在 `work/browser/`，以后可复用。

### 找不到 FFmpeg 或 FFprobe

安装包含两者的 FFmpeg，并将其 `bin` 目录加入 `PATH`；或设置：

```yaml
audio:
  ffmpeg_path: "C:\\tools\\ffmpeg\\bin\\ffmpeg.exe"
```

### 视频无法转写

确认 `asr.api_key` 有效、网络可访问 MiMo API，并检查 `work/run.log`。转写失败的作品会保留失败原因；修复后再次运行会继续处理未完成步骤。

### 下载失败或页面没有媒体地址

内容可能需要登录、已下架、受访问限制，或页面结构发生变化。先用可见浏览器模式重新运行并完成验证，再查看 `work/run.log` 中的具体错误。

### 如何只重新生成报告

运行 `python main.py report`。如需修改评论筛选规则，调整 `report.min_comment_chars` 或 `report.top_comments` 后再执行该命令，无需重新采集。

## 数据与安全

`config.yaml` 可能包含 MiMo API Key，`work/browser/` 可能包含登录会话。请勿将它们提交到 Git 仓库或发送给他人。

程序处理的是当前浏览器会话可访问的公开页面内容；页面限制、登录状态和抖音页面变化都会影响采集结果。报告会标注评论和作品列表是否确认完整及停止原因。
