# RecTheWord

**会议场景的双向实时翻译 + 录音 + AI 会议纪要**桌面应用（Windows CPU，无 GPU）。

基于 [LiveTranslate](https://github.com/TheDeathDragon/LiveTranslate)（MIT）改造：
保留其已验证的 Windows 链路（PyAudioWPatch WASAPI 采集 → Silero VAD →
faster-whisper/FunASR ASR worker → OpenAI 兼容流式翻译 → PyQt6 透明 overlay），
叠加会议差异化能力：

| 能力 | 说明 |
|---|---|
| **双路实时识别** | 麦克风（🎤 自己）与系统音频回环（🔊 对方）**各自独立** VAD + ASR，界面双栏分列显示。 |
| **两级显示** | interim 中间结果近零延迟上屏（灰色），整句识别完成后 final 原位替换（定色）。 |
| **三语识别** | 中 / 日 / 英自动语种检测（Whisper 多语模型；可切日语专用微调模型）。 |
| **双向翻译** | 对方 → 中文（默认开）；自己 → 目标语（默认关，可配，如中→英）。翻译只跟随 final。 |
| **全程录音** | 停止时落盘 `mix.wav`（双路混合）+ `mic.wav` + `sys.wav`（分轨）。 |
| **文字流记录** | 每句 `{时间戳, 路, 语种, 原文, 译文}` 写入 `transcript.jsonl`，可导出带标注文本。 |
| **会话管理** | 每次录制 = 一个会话；托盘菜单「会议会话管理」列出历史会话，可打开、补做精修/纪要、删除。 |
| **离线精修（手动）** | 停止后**默认不自动执行**；手动对 `mix.wav` 跑 SenseVoice+VAD+PUNC+CAM++ 说话人分离，得带说话人+时间戳全量稿。 |
| **说话人标注** | 精修稿中把匿名「说话人N」改为真实姓名（可编辑、持久化、即时刷新）。 |
| **会议纪要（手动）** | 二选一输入源：**A 离线精修稿**（带说话人）/ **B 实时文字流**（零额外计算），提交 OpenAI 兼容接口生成 Markdown 纪要，可导出。 |
| **远程 ASR** | 本地 CPU 不够时，把识别卸载到 GPU 机器（`asr_server.py`，见 [REMOTE_ASR.md](REMOTE_ASR.md)）。 |

---

## 系统要求

- **Windows 10 / 11 x64**
- **Python 3.10 – 3.12**
- CPU 即可（无 GPU 要求；GPU 可选加速）
- 网络（首次运行下载 ASR 模型）

## 安装与运行

```bat
:: 一次性安装（建虚拟环境 + 装依赖 + 检测 GPU + 预取常用 ASR 模型）
install.bat

:: 启动
start.bat
:: 或
.venv\Scripts\python.exe main.py
```

`install.bat` 会预取常用 ASR 模型，装完即可直接运行、首次启动无需再下载：

- **SenseVoice-Small**（默认引擎，约 900MB）← **ModelScope**
- **Whisper base / small**（常用备选，148 / 488MB）← **HF 镜像**
  （`https://hf-mirror.com`，优先）；镜像不通时回退 **GitHub Release** 附件

更大的模型（Fun-ASR-Nano、Whisper medium/large 等）由应用在设置里按需从
ModelScope / HF 镜像下载。首次启动还会走 SetupWizard 配置翻译/纪要 API（任意
OpenAI 兼容端点）。

> 注：模型不内嵌在 git 仓库（GitHub 单文件上限 100MB），而是安装时从
> ModelScope / HF 镜像（备用 Release）拉取——这保持了仓库小巧、可用普通 git
> 分发，同时实现开箱即用。

### 手动安装

```bat
python -m venv .venv
.venv\Scripts\activate
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

## 使用流程

1. 启动后托盘菜单 / 设置里选择**扬声器（回环）**与**麦克风**设备。
2. 点 **开始**：透明 overlay 出现双栏实时字幕——上栏 🔊 对方（原文+→中文译文），
   下栏 🎤 自己（原文+可选→目标语译文）。
3. 点 **停止**：会话自动归档（3 个 WAV + 文字流 + 元数据），**此后零后台计算**。
4. 托盘菜单「**会议会话管理**」→ 选中会话：
   - **离线精修**：手动跑说话人分离（耗时数十秒~数分钟）；
   - 精修完成后可编辑**说话人姓名**；
   - **生成纪要**：选「离线精修稿」或「实时文字流」→ AI 生成 Markdown → 可导出。

## 目录结构

```
main.py                 # 入口 + 双路管道编排（LanePipeline）
audio_capture.py        # PyAudioWPatch WASAPI 回环 + 麦克风（双路独立队列）
vad_processor.py        # Silero VAD（32ms，渐进/自适应静音）
asr_client.py / asr_worker.py / asr_engine.py 等   # ASR worker 进程 + 多后端
asr_remote.py / asr_server.py                     # 远程 ASR 客户端 / 服务端
translator.py           # OpenAI 兼容客户端（流式 / JSON schema / 上下文）
subtitle_overlay.py     # 透明 overlay（双栏：🔊 上 / 🎤 下，两级显示）
control_panel.py        # 设置 UI
model_manager.py        # 模型下载 / 缓存（ModelScope / HF 双源）
_recorder.py            # 会议 WAV 录音（mix/mic/sys）
_transcript_log.py      # 结构化文字流 JSONL
_sessions.py            # 会话存储层（索引 + 每会话目录）
_session_ui.py          # 会话管理窗口
_offline_diarize.py     # 离线精修（懒加载 FunASR 全家桶）
_labels.py              # 说话人姓名标注
_minutes.py             # 纪要生成（双输入源）
_refine_view.py         # 精修稿查看 + 说话人改名 + 纪要触发
config.yaml / user_settings.json
tests/                  # 单测（含会议模块）
_legacy_*               # 旧 FFmpeg 架构（已被本基底取代，仅供参考）
```

## 配置

- `config.yaml`：基础默认（音频设备、ASR 引擎/模型、翻译 API、字幕样式）。
- `user_settings.json`：运行时设置（模型、VAD 参数、ASR 引擎、`mic_target_language`、
  缓存路径等），优先级高于 config.yaml，原子写入防损坏。

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
| faster-whisper small int8（单实例，两路共享） | ~1GB |
| Silero VAD ×2 | ~0.2GB |
| PyAudioWPatch 采集 | ~0.2GB |
| **实时运行态合计** | **~2GB** |
| 离线精修（手动触发时才懒加载） | 峰值 ~5GB |

## 测试

```bat
.venv\Scripts\python.exe -m pytest tests/ -q
```

纯逻辑单测（无需安装 torch/funasr 全栈，但需要 numpy/pytest）。

## 许可

软件代码 MIT（源自 LiveTranslate，保留署名）；模型权重许可见各自模型卡。
