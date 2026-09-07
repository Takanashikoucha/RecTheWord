# RecTheWord 设计文档

## 1. 目标

Windows + PySide6 桌面应用，面向 **CPU（无 GPU）低延迟** 场景：

1. **FFmpeg** 枚举全部麦克风与扬声器，用户各选一个；通过 **WASAPI 共享模式** 抓取，
   麦克风流与扬声器回环流**分开**送入识别。
2. **实时字幕（带说话人区分）**：麦克风流与扬声器流**各自独立**实时识别，界面用
   `🎤 麦克风` / `🔊 扬声器` 区分显示；支持**中/英/日**。
3. **实时翻译**：可选配置外部 **OpenAI 兼容接口**，把识别文字实时翻译成中文并显示。
4. **结束后的离线整理**：对**混合录音**做**全量说话人分离**（CAM++ 聚类）+ 时间戳，
   并支持**说话人标注（真实姓名替换）**；再把带姓名+时间戳的全量稿交给 AI 生成
   **议题/讨论内容整理（会议纪要）**。

## 2. 关键技术结论（依据官方文档核实）

- **FFmpeg WASAPI**：`-f wasapi -shared 1`（共享模式，不独占设备）；扬声器回环用
  `loopback:<设备名>` 前缀。**只有 `dshow` 输入同时列出麦克风与扬声器**
  （`ffmpeg -f dshow -list_devices true -i dummy`）→ **dshow 枚举、wasapi 抓取**。
- **双路独立识别**：一次 ffmpeg 调用，两个 WASAPI 输入，各自 `aformat` 为 16 kHz mono，
  `join` 成**双声道 f32le**（左=麦、右=扬）输出到 stdout；应用内拆成两路单声道分别喂给
  两个独立流式 ASR 会话。同进程再 `amix` 出一路单声道 WAV 作为离线分离输入。
- **FunASR 实时**：`AutoModel(model="paraformer-zh-streaming", device="cpu")`，
  `generate(input=chunk, cache=cache, is_final=…, chunk_size=[0,10,5],
  encoder_chunk_look_back=4, decoder_chunk_look_back=1)`，stride = 10×960 = 9600 采样
  （600 ms）。**每条流一个独立 `cache`**（麦/扬各一）。
- **FunASR 离线全量分离**：`AutoModel(model="iic/SenseVoiceSmall",
  vad_model="fsmn-vad", punc_model="ct-punc", spk_model="cam++", device="cpu")`，
  `generate(input=wav, return_spk_res=True)` 返回 `sentence_info[]`，每句带
  `start`/`end`（毫秒）+ `spk`（**记录内匿名编号**）。SenseVoice 支持中/英/日自动判定。
- **说话人标注**：FunASR 的 `spk` 是记录内匿名簇，非真实身份、不跨记录稳定。
  应用层维护 `spk → 姓名` 映射（UI 可编辑、持久化），把匿名标签替换为实际姓名。
- **模型来源**：默认 `hub="ms"`（ModelScope），设置可切 `hub="hf"`（HuggingFace）。
- **FFmpeg 二进制**：Gyan Windows 静态构建，放 `vendor/ffmpeg`，`-loglevel error` 调子进程。

## 3. 架构

```
PySide6 UI (主线程)
   │  Qt signals
   ▼
rectheword/
  audio/
    devices.py        # dshow 枚举 麦/扬声器；ffprobe 定位 ffmpeg
    capture.py        # CaptureEngine：单条 ffmpeg 进程 → 双声道 f32le stdout + 混合 wav 文件
  pipeline/
    dual_pipeline.py  # 拆双声道 → 麦流 / 扬流 两路；各 9600 采样切块
    diarize.py        # 离线：SenseVoice+VAD+PUNC+CAM++ → [{start,end,spk,text}]
  asr/
    engine.py         # StreamingEngine(每流一实例,独立 cache) / OfflineEngine
  ai/
    client.py         # OpenAI 兼容 /chat/completions（requests，超时+重试）
    translate.py      # 实时翻译：按句节流
    minutes.py        # 结束：带姓名+时间戳全量稿 → 纪要
  labels.py           # 说话人标注：spk↔姓名 映射、持久化、应用到稿与纪要
  config.py           # JSON 持久化（设备、AI、ASR、hub、线程、说话人映射）
  ui/
    main_window.py    # 布局、状态机、信号接线
    settings_dialog.py
  main.py
models/  vendor/ffmpeg/  docs/  scripts/  tests/  recordings/
```

### 数据流（运行时）

```
ffmpeg( -f wasapi 麦 → [m] ；-f wasapi loopback 扬 → [s] )
  -map [m][s] join → stereo [out]  → stdout (f32le)
  -map [m][s] amix → mono [mix]    → recordings/<ts>.wav
        │
        ▼
dual_pipeline (QThread)  拆双声道 → mic_chunk / spk_chunk (各 9600 float32)
   ├─ mic_chunk → StreamingEngine(mic).feed() → on_mic_text → UI "🎤 麦克风"
   └─ spk_chunk → StreamingEngine(spk).feed() → on_spk_text → UI "🔊 扬声器"
   └─（可选）各自按句 → Translator → 中文译文面板
```

### 结束流程

```
停 ffmpeg → 两路 is_final 冲刷
   → 用 recordings/<ts>.wav（混合）跑 OfflineEngine (SenseVoice+VAD+PUNC+CAM++)
   → [{start,end,spk,text},…]
   → labels 套用/编辑 spk→姓名 → UI 显示分说话人全量稿
   →（若配置 AI）minutes 生成纪要（使用已标注姓名）
```

## 4. UI 布局

- 顶部：麦克风下拉、扬声器下拉、刷新；开始/停止；设置。
- 中部左：实时字幕（分路）—— `🎤 麦克风`、`🔊 扬声器` 两个独立滚动区（带时间戳）。
- 中部右：实时中文翻译（两路分别显示）。
- 下部左：说话人全量稿（结束后，按 `姓名/说话人N` 分组、带时间戳；可改名）。
- 下部右：会议纪要（结束后 AI 生成，Markdown 展示）。
- 底部状态栏：ffmpeg 状态 / 模型加载进度 / 离线分离进度 / 错误尾部。
- 启动自检：检测 ffmpeg；模型缺失提示将自动下载。

## 5. 模块职责

| 模块 | 职责 | 关键 API |
|---|---|---|
| `audio.devices` | 定位 ffmpeg；dshow 枚举麦/扬声器；wasapi 设备名构造 | `find_ffmpeg`, `list_audio_devices`, `list_render_devices` |
| `audio.capture` | 单条 ffmpeg 双路采集 + 混合 wav；子进程生命周期 | `build_ffmpeg_args`, `CaptureEngine.start/stop/read_stdout` |
| `pipeline.dual_pipeline` | 拆双声道、按 9600 采样切块、分路回调 | `split_stereo_f32le`, `DualAudioPipeline.chunk` 信号 |
| `asr.engine` | 流式/离线模型封装 | `StreamingEngine.feed/finalize`, `OfflineEngine.transcribe` |
| `pipeline.diarize` | 离线分离薄封装（引擎缓存） | `diarize_wav` |
| `labels` | spk↔姓名 映射、应用、纪要文本 | `SpeakerLabels.set_name/name_for/apply/to_minutes_text` |
| `ai.client` | OpenAI 兼容 /chat/completions | `OpenAICompatibleChat.chat/chat_single` |
| `ai.translate` | 按句节流增量翻译 | `Translator.feed/flush` |
| `ai.minutes` | 生成会议纪要 | `generate_minutes` |
| `config` | 配置 + 会话目录 + 模型目录 | `AppConfig.load/save`, `recordings_dir` |
| `ui.main_window` | 状态机、信号接线、设备下拉、面板 | `MainWindow` |

## 6. 状态机

```
idle ──开始──▶ loading(加载模型) ──完成──▶ recording(采集+实时)
   ▲              │(失败→idle)               │
   │              └─────────────────────────┤
   └──────────────── finishing(冲刷+离线分离) ┘
```

## 7. 风险与取舍

1. **无法在 Linux 验证 Windows 行为**（dshow 枚举、WASAPI、loopback、设备占用）。
   缓解：命令/解析单测 + README 调试命令与常见报错。
2. **两路实时 = 两个流式模型会话**，CPU 负载约为单路 2 倍；用 `ncpu` 与 `chunk_size` 调节。
3. **模型首跑下载**（Paraformer-streaming + SenseVoice + VAD + PUNC + CAM++，数百 MB 级）。
4. **实时分路仅区分“麦 vs 扬”**（非逐句精确到每个人）；逐人精确分离在结束时离线完成。
5. **纯日语实时**受 paraformer-zh-streaming 限制；离线 SenseVoice 对日更好。
6. **FFmpeg 二进制**需自备（脚本下载）；`vendor/ffmpeg` 不入库。
7. **AI 端点**需可达；未配置则翻译/纪要降级为“仅原始稿 + 可手动改名”。

## 8. 假设

- 目标机 Windows 10/11 x64，Python 3.10–3.12，有网络（首跑下载模型/ffmpeg）。
- FFmpeg 用 Gyan 静态 Windows 构建；FunASR 用 PyPI `funasr` + `torch/torchaudio`（CPU wheel）。
- 用户接受：实时 = 麦/扬两路分别标注；逐说话人精确分离 + 真实姓名标注 + 纪要 = 结束后离线产出。
