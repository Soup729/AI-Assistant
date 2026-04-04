# ClipMind AI

ClipMind AI 是一个面向 Windows 的桌面 AI 助手，围绕“选中文本 / 截图 OCR / 语音转文字 / 大模型流式回答 / 复制回填”这一套高频工作流设计。

## 主要能力

- 全局热键唤起主窗口
- 读取当前选中文本
- 截图并执行 OCR
- 本地 RapidOCR
- 可选云端 OCR API
- 可选混合 OCR 增强
- 大模型流式响应
- 复制结果
- 自动回填到原应用窗口
- Prompt 模板管理
- 联网搜索与正文提取
- 多模型档案管理
- 在主窗口里自由切换已配置好的模型
- 主窗口支持拖动
- `Ctrl + Alt + R` 录音转文字
- 保存配置和历史记录

## 快速开始

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

### 3. 配置 OCR

在设置窗口的“增强功能”页里：

- 选择 OCR 引擎
- 如果使用云端 OCR API，填写接口地址、Key、图片字段名和结果文本路径
- 如果使用混合模式，程序会优先使用本地 RapidOCR，再按需要调用云端接口增强结果

## 打包

推荐直接使用仓库里的 spec 文件：

```powershell
python -m PyInstaller --noconfirm --clean ClipMindAI.spec
```

## 运行时数据

程序会把配置、数据库和日志放到用户目录，不再写回打包目录。

默认路径：

`%LOCALAPPDATA%\ClipMindAI`

## 详细说明

更完整的目录说明、配置说明和故障排查见：

- [clipmind_ai/README.md](clipmind_ai/README.md)
