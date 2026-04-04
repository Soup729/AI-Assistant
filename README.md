# ClipMind AI

ClipMind AI 是一个面向 Windows 的桌面 AI 助手，适合“选中文本 -> 读取 -> 发送给大模型 -> 流式返回 -> 一键复制或回填”的工作流。

它支持：

- 全局热键唤起主窗口
- 读取当前选中文本
- 截图 OCR 识别
- 大模型流式响应
- 复制结果
- 自动回填到原来的应用窗口
- Prompt 模板管理
- 联网检索和正文提取
- 本地保存配置和历史记录

## 快速开始

### 1. 准备环境

建议使用 Python 3.11 或 3.12，Windows 10 / 11 可用。

```powershell
cd F:\AI-Assistant
python -m venv .venv
.venv\Scripts\activate
pip install -r clipmind_ai/requirements.txt
```

### 2. 运行程序

```powershell
python clipmind_ai/app/main.py
```

### 3. 配置模型

打开右上角设置，填写：

- `API Base URL`
- `API Key`
- `Model Name`
- `Temperature`
- `Max Tokens`

常见示例：

- OpenAI: `https://api.openai.com/v1`
- DeepSeek: `https://api.deepseek.com`
- 智谱: `https://open.bigmodel.cn/api/paas/v4`
- 通义千问兼容模式: `https://dashscope.aliyuncs.com/compatible-mode/v1`

## 主要功能

- `Alt + Space`：显示或隐藏主窗口
- `Alt + A`：读取当前选中文本
- `Alt + S`：截图并执行 OCR
- `复制结果`：把当前回答复制到剪贴板
- `自动回填`：把当前回答粘贴回原来的应用窗口
- 关闭按钮：真正退出程序，不只是隐藏窗口

## 流式响应

当前大模型响应是流式显示的。

表现为：

- 发出请求后会显示等待状态和已等待时间
- 模型开始返回内容后，文本会逐段实时追加到输出框
- 不会等整段答案完成后才一次性出现

## 打包

推荐直接使用项目里的 spec：

```powershell
python -m PyInstaller --noconfirm --clean ClipMindAI.spec
```

如果你要自己调整参数，可以查看：

- [clipmind_ai/app/main.py](clipmind_ai/app/main.py)
- [ClipMindAI.spec](ClipMindAI.spec)

## 运行时数据

程序会把运行时数据放到用户目录，不再写入打包目录：

- 配置文件
- 数据库
- 日志

Windows 下默认位置：

`%LOCALAPPDATA%\ClipMindAI`

## GitHub 上传建议

上传仓库前，请确认这些生成物不要提交：

- `build/`
- `dist/`
- `logs/`
- `.clipmind_data/`
- `app_data.db`
- `config.json`

仓库里已经准备好 `.gitignore`，可以直接用。

## 详细文档

如果你想看更完整的说明、目录结构和故障排查，请看：

- [clipmind_ai/README.md](clipmind_ai/README.md)

