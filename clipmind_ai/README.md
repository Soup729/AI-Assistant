# ClipMind AI

ClipMind AI 是一个面向 Windows 的桌面 AI 助手，核心目标是把“选中文本 / 截图 OCR / 语音转文字 / 大模型生成 / 复制回填”串成一条尽量顺手的工作流。

## 项目目标

目标是提供一个轻量、可维护、可打包为 Windows `exe` 的桌面 AI 工具，覆盖这些场景：

- 在任意应用中通过全局热键唤起主窗口
- 读取当前应用里的选中文本并填入输入框
- 对屏幕选区截图并执行 OCR
- 使用 Prompt 模板快速完成翻译、解释、润色等任务
- 支持自定义大模型 API 和模型名
- 支持按模板启用联网检索
- 支持复制回答或自动粘贴回当前应用
- 保存配置、模板和会话历史
- 支持录音转文字
- 同时采集系统音频和麦克风输入

## 当前功能

### 1. 主窗口

主窗口由 [app/ui/main_window.py](app/ui/main_window.py) 提供，包含：

- 当前模型下拉框
- 输入框
- 输出框
- 模板下拉框
- OCR 状态
- AI 响应状态
- `复制结果`
- `自动回填`
- 窗口可拖动

关闭按钮会真正退出程序，不再只是隐藏窗口。

### 2. 全局热键

默认热键如下：

- `Alt + Space`：显示 / 隐藏主窗口
- `Alt + A`：读取选中文本
- `Alt + S`：截图 OCR
- `Ctrl + Alt + R`：开始录音，再按一次停止并转文字

热键可以在设置页中修改，保存后立即生效。

### 3. 录音转文字

在“增强功能”页里填写语音模型目录。
目录中需要包含 `model.onnx` 和 `tokens.txt`，建议直接放一个 sherpa-onnx 的 SenseVoice 模型文件夹。

录音功能会同时采集：

- 系统音频
- 麦克风输入

第二次按下快捷键后会停止录音并执行离线识别，识别结果会直接回填到输入框。

### 4. 读取选中文本

实现流程：

- 释放可能残留的修饰键
- 模拟 `Ctrl + C`
- 从 Windows 剪贴板读取 Unicode 文本
- 把原剪贴板内容恢复回去
- 回填到主窗口输入框

特点：

- 使用 Win32 API 操作剪贴板
- 带锁，减少并发污染
- 尽量把原剪贴板内容恢复回来

### 5. 截图 OCR

当前 OCR 方案已经切换为 RapidOCR。

支持三种模式：

- `RapidOCR`
- `云端 OCR API`
- `混合增强（本地 + 云端）`

#### 本地 RapidOCR

- 更轻
- 更适合桌面截图场景
- 首次使用时可能需要完成模型初始化或下载

#### 云端 OCR API

设置页里可以配置：

- API 地址
- API Key
- 图片字段名
- 结果文本路径

默认请求方式为 `multipart/form-data`，上传字段名默认是 `image_file`。

#### 混合增强

混合模式会先跑本地 RapidOCR。
如果本地结果较少、置信度偏低，或者识别不够完整，就会再调用云端接口做增强。

### 6. Prompt 模板

支持：

- 初始化默认模板
- 新增模板
- 编辑模板
- 删除模板
- 开关模板是否启用联网检索

默认模板包括：

- 通用问答
- 解释说明
- 中英翻译
- 文本润色

### 7. 大模型调用

当前通过 OpenAI-compatible 接口调用，支持：

- OpenAI
- DeepSeek
- 智谱
- 通义千问兼容接口
- 其它兼容 `chat/completions` 的服务

现在支持多模型档案管理：

- 每个模型档案可单独保存 `API Base URL`、`API Key`、`Model Name`、`Temperature` 和 `Max Tokens`
- 设置页可以新增、编辑、删除多个模型档案
- 主窗口顶部可以在已保存的模型之间自由切换
- 切换后请求会立刻使用当前选中的模型档案

请求方式：

- 使用 `httpx` 发起流式请求
- 返回内容会实时追加到输出框
- 首个 token 到达前会显示等待时间

### 8. 联网检索与正文抓取

按模板可以开启联网检索。

流程：

- 先调用搜索 API 获取结果列表
- 读取前几条 URL
- 抓取网页正文
- 把正文摘要注入 Prompt 上下文

当前支持：

- 搜索服务：Tavily 风格 API
- 网页抓取：`requests` + `BeautifulSoup4` + `trafilatura`

### 9. 本地存储

程序会保存：

- 配置文件
- SQLite 数据库
- 会话历史
- Prompt 模板
- 日志

这些运行时数据会落在用户目录下，避免写入打包目录。

Windows 默认位置：

`%LOCALAPPDATA%\ClipMindAI`

## 目录结构

```text
clipmind_ai/
├─ app/
│  ├─ main.py
│  ├─ core/
│  │  ├─ clipboard_service.py
│  │  ├─ content_extractor.py
│  │  ├─ hotkey_manager.py
│  │  ├─ llm_client.py
│  │  ├─ ocr_service.py
│  │  ├─ prompt_engine.py
│  │  ├─ search_service.py
│  │  └─ speech_service.py
│  ├─ storage/
│  │  ├─ config.py
│  │  └─ db.py
│  ├─ ui/
│  │  ├─ main_window.py
│  │  ├─ overlay_window.py
│  │  └─ settings_window.py
│  └─ utils/
│     ├─ error_handler.py
│     ├─ logger.py
│     ├─ runtime_paths.py
│     └─ text_cleaner.py
├─ assets/
├─ requirements.txt
└─ README.md
```

## 运行环境

推荐环境：

- Windows 10 / Windows 11
- Python 3.11 或 3.12
- 普通 CPU 即可
- 建议使用虚拟环境

## 安装步骤

### 1. 进入项目目录

```powershell
cd F:\AI-Assistant
```

### 2. 创建虚拟环境

推荐：

```powershell
python -m venv .venv
.venv\Scripts\activate
```

如果你使用 Conda，也可以：

```powershell
conda create -n clipmind-ai python=3.11 -y
conda activate clipmind-ai
```

### 3. 安装依赖

```powershell
pip install -r clipmind_ai/requirements.txt
```

## 启动方式

在项目根目录执行：

```powershell
python clipmind_ai/app/main.py
```

如果你已经进入 `clipmind_ai` 目录，也可以：

```powershell
python app/main.py
```

## 配置说明

### 1. 大模型 API

打开设置页后，填写：

- API Base URL
- API Key
- Model Name
- Temperature
- Max Tokens

常见示例：

- OpenAI: `https://api.openai.com/v1`
- DeepSeek: `https://api.deepseek.com`
- 智谱: `https://open.bigmodel.cn/api/paas/v4`
- 通义千问兼容模式: `https://dashscope.aliyuncs.com/compatible-mode/v1`

### 2. OCR 引擎

设置页的“增强功能”里可以选择：

- `本地 RapidOCR`
- `云端 OCR API`
- `混合增强（本地 + 云端）`

云端 OCR 相关字段：

- `云端 OCR 地址`
- `云端 API Key`
- `图片字段名`
- `结果文本路径`
- `云端超时(秒)`

说明：

- 默认图片字段名是 `image_file`
- 如果你的云端接口返回的文本不在默认字段里，可以在“结果文本路径”里填写类似 `text`、`data.text`、`result.txts` 的路径
- 如果你只是想调用兼容 RapidOCRAPI 的服务，通常保留默认值就行

### 3. 录音转文字

设置页可配置：

- 语音模型目录
- 录音快捷键

语音模型目录中需要包含 `model.onnx` 和 `tokens.txt`。

### 4. 热键

设置页可修改：

- 唤起窗口
- 读取选中文本
- 截图 OCR
- 录音转文字

保存后立即生效。

### 5. Prompt 模板

设置页可：

- 新增模板
- 编辑模板
- 删除模板
- 开关联网检索

## 打包部署

推荐使用项目里的 spec 文件：

```powershell
python -m PyInstaller --noconfirm --clean ClipMindAI.spec
```

说明：

- 这是 `onedir` 形式的打包
- 已去掉旧 OCR 相关依赖和模型资产
- RapidOCR 相关依赖会按需收集
- 运行时数据不会写回打包目录

如果你想自己调整打包参数，请优先查看：

- [../ClipMindAI.spec](../ClipMindAI.spec)
- [main.py](app/main.py)

## 常见问题

### 1. 为什么 OCR 首次启动比较慢

本地 RapidOCR 首次使用时可能需要完成模型初始化或下载，所以第一次会慢一些。

如果你希望完全避免本地模型初始化带来的等待，可以直接切到云端 OCR API。

### 2. 为什么 AI 回复是逐段出现的

这是流式响应。

好处：

- 用户能马上看到模型开始输出
- 避免“点了发送但界面空白”的不确定感
- 长回答更有反馈

### 3. 为什么关闭按钮现在会真正退出

此前只是隐藏窗口，热键线程和主进程还在。

现在关闭动作会：

- 停止热键线程
- 取消语音录音
- 退出 Qt 事件循环
- 结束主进程

### 4. 复制结果和自动回填的区别

- `复制结果`：只把输出写进剪贴板
- `自动回填`：先恢复原窗口焦点，再执行粘贴，并在几十毫秒后恢复原剪贴板文本

### 5. 为什么运行时数据不放在程序目录

因为打包目录通常是只读或容易被锁定。

现在会放在用户目录下，默认位置是：

`%LOCALAPPDATA%\ClipMindAI`

这样更稳定，也更适合打包后的 exe。

## 相关文件

- 启动入口：[app/main.py](app/main.py)
- OCR 服务：[app/core/ocr_service.py](app/core/ocr_service.py)
- 语音转文字：[app/core/speech_service.py](app/core/speech_service.py)
- 剪贴板与回填：[app/core/clipboard_service.py](app/core/clipboard_service.py)
- 主窗口：[app/ui/main_window.py](app/ui/main_window.py)
- 设置页：[app/ui/settings_window.py](app/ui/settings_window.py)
- 配置管理：[app/storage/config.py](app/storage/config.py)
- 数据库：[app/storage/db.py](app/storage/db.py)
- 依赖清单：[requirements.txt](requirements.txt)
