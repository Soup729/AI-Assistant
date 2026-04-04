# ClipMind AI

ClipMind AI 是一款面向 Windows 的桌面智能助手，围绕「选中文本 / 截图 OCR / 语音转文字 / 大模型流式回答 / 复制回填」这一高频工作流设计，帮助你在日常操作中更高效地获取和处理信息。

## ✨主要能力

| 功能模块 | 说明 |
|---------|------|
| 🎤 语音识别 | 基于 sherpa-onnx 的实时录音转文字 |
| 📷 OCR 识别 | 截图 OCR，支持本地 RapidOCR + 云端 API 混合增强 |
| 📋 选中文本 | 全局热键快速读取当前选中内容 |
| 🤖 大模型对话 | 流式响应，支持多模型管理与 Prompt 模板 |
| 🌐 联网搜索 | 可选联网搜索增强大模型回答 |
| ⚙️ 系统交互 | pywin32 增强剪贴板与模拟按键 |
| 💾 数据管理 | 配置、历史记录自动保存 |

## 🚀快速开始

### 1. 安装依赖

```powershell
cd F:\AI-Assistant
python -m venv .venv
.venv\Scripts\activate
pip install -r clipmind_ai/requirements.txt
```

### 2. 启动程序

```powershell
python clipmind_ai/app/main.py
```

### 3. 配置说明

在设置窗口里：

- 模型配置：API Base URL、API Key与Model Name
- 语言目录配置：指定语言模型的目录路径，需要包含 `model.onnx` 和 `tokens.txt`
- 可选配置：联网搜索API，混合OCR配置

## 📦打包

推荐直接使用仓库里的 spec 文件：

```powershell
python -m PyInstaller --noconfirm --clean ClipMindAI.spec
```

## 📁运行时数据

程序会把配置、数据库和日志放到用户目录，不再写回打包目录。

默认路径：

`%LOCALAPPDATA%\ClipMindAI`

## 📖详细说明

更完整的目录说明、配置说明和故障排查见：

- [clipmind_ai/README.md](clipmind_ai/README.md)

## 🔮未来计划

详见 [未来优化方案](./FUTURE.md)

## 📝更新日志

### 2026-04-05
- 新增 sherpa-onnx 离线语音识别
- OCR 引擎从 PaddleOCR 替换为 RapidOCR
- 引入 pywin32 优化剪贴板操作
- 优化 UI 界面布局