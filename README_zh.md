# RecTheWord

**会议场景的双向实时翻译 + 录音 + AI 会议纪要**桌面应用（Windows，CPU 即可）。

[English](README.md) | **中文**

基于 [LiveTranslate](https://github.com/TheDeathDragon/LiveTranslate)（MIT）改造，
保留其已验证的 Windows 链路，叠加会议差异化能力。

## 功能一览

| 能力 | 说明 |
|---|---|
| 双路实时识别 | 麦克风（🎤 自己）与系统音频回环（🔊 对方）各自独立 VAD + ASR，双栏分列显示 |
| 两级显示 | interim 中间结果近零延迟上屏（灰色），整句 final 原位替换（定色） |
| 三语识别 | 中 / 日 / 英自动语种检测（SenseVoice / Whisper 多语模型） |
| 双向翻译 | 对方→中文（默认开）；自己→目标语（可配，如中→英） |
| 全程录音 | 停止时落盘 `mix.wav` + `mic.wav` + `sys.wav` |
| 文字流记录 | 每句 `{时间戳, 路, 语种, 原文, 译文}` 写入 `transcript.jsonl` |
| 会话管理 | 每次录制 = 一个会话；托盘菜单可打开历史会话、补做精修/纪要、删除 |
| 离线精修（手动） | 停止后默认不自动执行；手动跑说话人分离得带说话人+时间戳全量稿 |
| 说话人标注 | 精修稿中把「说话人N」改为真实姓名（可编辑、持久化） |
| 会议纪要（手动） | 二选一输入源（离线精修稿 / 实时文字流）→ AI 生成 Markdown |
| 远程 ASR | 本地 CPU 不够时把识别卸载到 GPU 机器（见 [REMOTE_ASR.md](REMOTE_ASR.md)） |

## 开箱即用

`install.bat` 安装时会从 **ModelScope** 预取默认的轻量 ASR 模型
（SenseVoice-Small，约 900MB），因此装完环境即可直接运行、首次启动无需再
下载模型。更大的模型（Fun-ASR-Nano、Whisper medium/large 等）由应用按需从
ModelScope 下载（设置里可切换）。

> 注：模型不内嵌在 git 仓库（GitHub 单文件上限 100MB），而是安装时从
> ModelScope 拉取——这保持了仓库小巧、可用普通 git 分发，同时实现开箱即用。

## 系统要求

- Windows 10 / 11 x64
- Python 3.10 – 3.12
- CPU 即可（无 GPU 要求；GPU 可选加速）

## 安装与运行

```bat
:: 一次性安装（建虚拟环境 + 装依赖 + 检测 GPU）
install.bat

:: 启动
start.bat
:: 或
.venv\Scripts\python.exe main.py
```

首次启动走 SetupWizard（选下载源 + 配置翻译 API），随后即可开始会议录制。

### 手动安装

```bat
python -m venv .venv
.venv\Scripts\activate
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

## 使用流程

1. 启动后在托盘菜单 / 设置里选择**扬声器（回环）**与**麦克风**设备。
2. 点 **开始**：透明 overlay 出现双栏实时字幕——上栏 🔊 对方（原文+→中文译文），
   下栏 🎤 自己（原文+可选→目标语译文）。
3. 点 **停止**：会话自动归档（3 个 WAV + 文字流 + 元数据），**此后零后台计算**。
4. 托盘菜单「**会议会话管理**」→ 选中会话：
   - **离线精修**：手动跑说话人分离；
   - 精修完成后可编辑**说话人姓名**；
   - **生成纪要**：选「离线精修稿」或「实时文字流」→ AI 生成 Markdown → 可导出。

## 会话存储布局

```
~/.rectheword/sessions/
  index.json                          # 会话索引
  <YYYYMMDD-HHMMSS>/
    meta.json       # 设备 / ASR 后端 / 翻译配置快照
    mix.wav / mic.wav / sys.wav
    transcript.jsonl
    refined.json    # （手动精修后）
    labels.json     # 说话人姓名映射
    minutes.md      # （手动生成后）
```

## 内存预算（16G 机器）

| 组件 | 估算 |
|---|---|
| PyQt6 + 应用 | ~0.5GB |
| SenseVoice-Small（CPU int8，两路共享单 worker） | ~1GB |
| Silero VAD ×2 | ~0.2GB |
| PyAudioWPatch 采集 | ~0.2GB |
| **实时运行态合计** | **~2GB** |
| 离线精修（手动触发时才懒加载） | 峰值 ~5GB |

## 许可

软件代码 MIT（源自 LiveTranslate，保留署名）；模型权重许可见各自模型卡。
