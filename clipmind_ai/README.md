# ClipMind AI

ClipMind AI 是一个面向 Windows 的桌面 AI 助手，围绕“读取选中文本、截图 OCR、调用大模型、复制结果、自动回填”这一套高频工作流设计。

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

## 当前功能

### 1. 主窗口

主窗口由 [app/ui/main_window.py](app/ui/main_window.py) 提供，包含：

- 输入框
- 输出框
- 模板下拉框
- OCR 状态
- AI 响应状态
- `复制结果`
- `自动回填`

现在关闭按钮会真正退出程序，不再只是隐藏窗口。

### 2. 全局热键

默认热键如下：

- `Alt + Space`：显示 / 隐藏主窗口
- `Alt + A`：读取选中文本
- `Alt + S`：截图 OCR

热键可以在设置页中修改，保存后立即生效。

### 3. 读取选中文本

实现流程：

- 释放可能残留的修饰键
- 模拟 `Ctrl + C`
- 从 Windows 剪贴板读取 Unicode 文本
- 回填到主窗口输入框

特点：

- 不依赖外部进程
- 尽量恢复原剪贴板内容
- 兼容中英文文本

### 4. 截图 OCR

截图流程：

- 捕获当前屏幕
- 显示区域遮罩
- 选择区域后传入 OCR 服务

OCR 支持：

- `fast` 模式
- `accurate` 模式

模型优先级大致为：

1. 打包内置模型
2. 项目目录 `assets/models`
3. 用户目录缓存

### 5. Prompt 模板

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

### 6. 大模型调用

当前通过 OpenAI-compatible 接口调用，支持：

- OpenAI
- DeepSeek
- 智谱
- 通义千问兼容接口
- 其它兼容 `chat/completions` 的服务

支持配置：

- API Base URL
- API Key
- Model Name
- Temperature
- Max Tokens

请求方式：

- 使用 `httpx` 发起流式请求
- 返回内容会实时追加到输出框
- 首个 token 到达前会显示等待时间

### 7. 联网检索与正文抓取

按模板可以开启联网检索。

流程：

- 先调用搜索 API 获取结果列表
- 读取前几条 URL
- 抓取网页正文
- 把正文摘要注入 Prompt 上下文

当前支持：

- 搜索服务：Tavily 风格 API
- 网页抓取：`requests` + `BeautifulSoup4` + `trafilatura`

### 8. 本地存储

程序会保存：

- 配置文件
- SQLite 数据库
- 会话历史
- Prompt 模板
- 日志

现在这些运行时数据会落在用户目录下，避免写入打包目录。

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
│  │  └─ search_service.py
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
│  └─ models/
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

### 2. OCR 模式

设置页的增强功能里可以选择：

- `fast`
- `accurate`

建议：

- 日常截图文字识别优先用 `fast`
- 小字、复杂版面可试 `accurate`

### 3. 热键

设置页可修改：

- 唤起窗口
- 读取选中文本
- 截图 OCR

保存后立即生效。

### 4. Prompt 模板

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
- OCR 模型和 Paddle 相关依赖会一起打包
- 运行时数据不会写回打包目录

如果你想自己调整打包参数，请优先查看：

- [../ClipMindAI.spec](../ClipMindAI.spec)
- [main.py](app/main.py)

## 常见问题

### 1. 为什么 OCR 首次启动比较慢

PaddleOCR 需要加载模型和依赖，第一次启动会慢一些。

当前优化包括：

- 程序启动后后台预加载 OCR
- 优先使用项目内置模型

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
- 退出 Qt 事件循环
- 结束主进程

### 4. 复制结果和自动回填的区别

- `复制结果`：只把输出写进剪贴板
- `自动回填`：先恢复原窗口焦点，再执行粘贴

### 5. 为什么运行时数据不放在程序目录

因为打包目录通常是只读或容易被锁定。

现在会放在用户目录下，默认位置是：

`%LOCALAPPDATA%\ClipMindAI`

这样更稳定，也更适合打包后的 exe。

## 相关文件

- 启动入口：[app/main.py](app/main.py)
- OCR 服务：[app/core/ocr_service.py](app/core/ocr_service.py)
- 主窗口：[app/ui/main_window.py](app/ui/main_window.py)
- 设置页：[app/ui/settings_window.py](app/ui/settings_window.py)
- 配置管理：[app/storage/config.py](app/storage/config.py)
- 数据库：[app/storage/db.py](app/storage/db.py)
- 依赖清单：[requirements.txt](requirements.txt)
