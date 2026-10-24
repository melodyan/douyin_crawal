# 采集工作台前端

Vue 3 + Vite + TypeScript 单页工作台，通过 FastAPI 调用本机 Python 采集流程。

## 启动

在 `frontend` 目录执行：

```powershell
npm install
npm run dev
```

另开一个终端，在项目根目录启动后端：

```powershell
uvicorn api:app --host 127.0.0.1 --port 8000
```

## 页面

- **任务列表**：查看持久化任务、实时进度、处理步骤和错误信息；支持创建、取消、重试、重新转写、更新评论和删除任务记录。
- **采集配置**：从后端加载并保存本机 `config.yaml`。保存前会校验字段；MiMo API Key 只显示是否已设置，输入框留空会保留已有密钥。
- **临时配置**：创建任务时开启后，可选择覆盖评论上限、作者/合集作品上限和已完成任务重跑选项；其余配置沿用配置页面的默认值。

默认后端地址为 `http://127.0.0.1:8000`，可通过 `VITE_API_BASE` 修改。任务状态保存在 SQLite，并通过 SSE 推送到页面。
