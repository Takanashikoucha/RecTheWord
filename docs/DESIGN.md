# RecTheWord 设计文档

## 1. 目标

Windows + PyQt6 桌面应用，面向**会议场景、CPU（无 GPU）低延迟**：

1. **双路实时识别 + 翻译**：麦克风（🎤 自己）与系统音频回环（🔊 对方）各自独立
   VAD + ASR，透明 overlay 双栏分列显示；中/日/英自动语种检测。
2. **两级显示**：interim 中间结果近零延迟上屏，整句 final 原位替换。
3. **全程录音**：停止时落盘 mix/mic/sys 三路 WAV。
4. **文字流记录**：结构化 JSONL（时间戳/路/语种/原文/译文）。
5. **会话管理**：每次录制 = 一个会话；历史会话可打开、补做精修/纪要、删除。
6. **离线精修 + 说话人标注 + 会议纪要**：停止后**默认不自动执行**，全部手动触发；
   纪要支持「离线精修稿 / 实时文字流」二选一输入源。
7. **远程 ASR**：本地 CPU 不足时卸载到 GPU 机器。

## 2. 基底与技术结论

以 [LiveTranslate](https://github.com/TheDeathDragon/LiveTranslate)（MIT，已验证
Windows 链路）为基底改造，**弃用旧 FFmpeg/WASAPI 子进程采集路线**（未经验证）。

- **采集**：`PyAudioWPatch` WASAPI 回环 + 麦克风，原生 44.1kHz 采集、内部重采样
  16kHz mono、32ms chunk（Silero VAD 原生窗口）；设备热切换自动重连。
- **双路**：`AudioCapture` 拆出 `audio_queue`（sys）与 `mic_queue`（mic）两个独立
  队列，`get_audio()` / `get_mic_audio()` 分路取；mic 不再混入回环。
- **VAD**：Silero v5（`silero-vad` PyPI 包内置模型，零网络），渐进静音
  （<3s 全量 / 3-6s 半 / 6-10s 四分之一）+ 自适应静音（P75×1.2，0.3~2.0s）+
  回溯切分 + 语音密度过滤。
- **ASR**：GUI 进程不直接持有模型；`ASRClient` 管理一个 `ASRWorker` 子进程
  （`multiprocessing` spawn + Pipe IPC + 超时），worker 拥有具体后端/模型。
  后端：faster-whisper（主，base/small int8）、FunASR（SenseVoice/Nano）、
  Anime-Whisper、远程 Whisper（HTTP）。两路**共享同一个 worker**（串行推理）。
- **增量 ASR（两级显示核心）**：VAD 累积期间周期性跑 interim ASR →
  `yasbd` 分句（40 语言规则式 + 逗号回退）→ 提交完整句 + 比例音频 trim
  （+0.3s 余量，保留 ≥0.5s）+ 回声去重（committed tail 匹配）+ 短句缓冲
  （≤8 字母数字字符并入下句）。VAD flush 时走 final 路径。
- **翻译**：OpenAI 兼容客户端（流式 `translate_iter` / JSON schema / 上下文历史 /
  思考模型禁用策略），按句异步，只跟随 final。
- **UI**：PyQt6 透明 overlay（always-on-top、点击穿透、可拖拽、14 主题），
  本应用改造为**双栏**（上 🔊 / 下 🎤，各自独立滚动）。

## 3. 架构

```
PyQt6 UI（主线程）
   │ Qt signals
   ▼
main.py (LiveTranslateApp)   # 顶层入口；下列模块均在 app/ 包内
  ├── audio_capture.py     WASAPI 回环 + 麦克风（双路独立队列）
  ├── vad_processor.py     Silero VAD（每路一个实例）
  ├── LanePipeline         每路的 VAD + 增量 ASR 状态机 + 段队列（本应用新增）
  ├── asr_client.py        ASR worker 进程管理（两路共享单 worker）
  ├── translator.py        OpenAI 兼容流式翻译
  ├── subtitle_overlay.py  透明 overlay（双栏：🔊 上 / 🎤 下）
  ├── _recorder.py         会议 WAV 录音（mix/mic/sys）
  ├── _transcript_log.py   结构化文字流 JSONL
  ├── _sessions.py         会话存储层
  ├── _session_ui.py       会话管理窗口
  ├── _offline_diarize.py  离线精修（懒加载）
  ├── _labels.py           说话人姓名标注
  ├── _minutes.py          纪要生成（双输入源）
  ├── _refine_view.py      精修稿查看 + 改名 + 纪要触发
  └── model_manager.py     模型下载/缓存
```

### 数据流（运行时）

```
PyAudioWPatch（WASAPI 回环 + 麦克风）
  ├─ audio_queue (sys) ─┐
  └─ mic_queue   (mic) ─┴→ 各自 LanePipeline
        ├─ VAD 累积 → 周期 interim ASR → 分句提交（on_commit，灰色上屏）
        ├─ VAD flush → final ASR（on_final，定色原位替换）
        └─ 录音旁路 → _recorder（累积两路）
  共享 ASR worker（串行推理）
  on_final → _emit_text(lane)
        ├─ overlay 对应栏 add_message
        ├─ _transcript_log.log_final
        └─ 翻译（sys→中文 恒开；mic→目标语 可配）→ 流式上屏 + set_translation
停止时：
  冲刷两路 VAD（flush_remaining）
  → _recorder.finish() 写 mix/mic/sys.wav
  → _transcript_log.close()
  → _sessions.close() 归档（此后零后台计算）
```

### 会后手动流程

```
会话管理窗口 → 选中会话
  ├─ 离线精修 → _offline_diarize.diarize_wav(mix.wav)
  │            → refined.json（[{start_ms,end_ms,spk,text}]）
  ├─ 精修稿查看（_refine_view）→ 说话人改名（labels.json 持久化）
  └─ 生成纪要 → 二选一：
       A refined：refined.json + labels → generate_from_segments
       B stream ：transcript.jsonl 回放 → generate_from_text_stream
       → minutes.md（可导出）
```

## 4. 关键设计决策

### 4.1 LanePipeline（双路复用）
把上游单路的「VAD + 增量 ASR 状态机 + 段队列」抽成 `LanePipeline` 类，
每路一个实例（`sys` / `mic`），通过回调（`run_asr` / `asr_ready` /
`on_commit` / `on_final`）接入宿主 App，从而**两路共享单一 ASR worker**、
避免数百行逻辑复制。`_capture_loop` 轮询两路队列喂 VAD；`_asr_loop` 单消费者
排空两路段队列。

### 4.2 两级显示
- **interim**：VAD 累积 ≥ `interim_interval` 时周期跑低延迟 ASR，`yasbd` 分句后
  提交完整句（`on_commit`），overlay 灰色即时上屏；
- **final**：VAD 判定句子结束 → final ASR（`on_final`）→ 定色原位替换；
- 翻译只跟随 final（省 token、防抖动）；interim 抖动靠同句覆盖自然收敛。

### 4.3 会话与零后台计算
停止即归档（WAV + 文字流 + 元数据），**不自动跑精修/纪要**；二者都是用户在
（当前或历史）会话上的手动按钮动作，异步执行、可取消。会话目录布局见 README。

### 4.4 翻译双向化
`_emit_text(lane, ...)` 统一出口：sys 路 → 主目标语（中文，恒开）；
mic 路 → `mic_target_language`（默认空 = 不翻译，可配如 `en`）。同源语言跳过。

## 5. 边缘情况与失败模式

- **回环不可用** → 降级仅 mic 单路（上游 `_loopback_disabled` 机制）；纪要仍可用文字流源。
- **whisper 模型首跑下载** → SetupWizard / ModelDownloadDialog 流程覆盖；`cache_path` 可预置。
- **远程 ASR 断线** → 健康检查告警 + 可配置回落本地。
- **CPU 过载** → 两路共享单 worker 串行推理 + VAD 门控；远程 ASR 为逃生通道。
- **AI 端点不可达** → 降级仅原文 + 可导出文字流/wav 稍后提交。
- **会话目录损坏/缺文件** → 列表标「不完整」，仍可打开现存部分。
- **精修/纪要中途取消** → 半成品不落盘，状态回滚。
- **空会话** → 无音频时不落盘 0 字节 WAV（`_recorder.finish` 早退）。

## 6. 内存预算（16G 机器）

| 组件 | 估算 |
|---|---|
| PyQt6 + 应用 | ~0.5GB |
| faster-whisper small int8（单实例，两路共享） | ~1GB |
| Silero VAD ×2 | ~0.2GB |
| PyAudioWPatch 采集 | ~0.2GB |
| **实时运行态合计** | **~2GB** |
| 离线精修（手动触发时才懒加载） | 峰值 ~5GB |

## 7. 假设

- Windows 10/11 x64，Python 3.10–3.12，首跑有网。
- 实时 = interim 近零延迟 + final 1-3 秒定稿；翻译只跟 final。
- 实时界面「🎤自己 / 🔊对方」两路；逐说话人分离只在手动离线精修时做。
- 停止后默认零后台计算；精修/纪要全手动。
- 保留 LiveTranslate MIT 许可与署名。

## 8. 验收标准

1. 纯逻辑自测全绿（两级显示事件、会话 CRUD、双源纪要、labels 持久化）。
2. overlay 透明置顶、点击穿透、双栏两级显示（interim 灰 → final 定色原位替换）。
3. 停止后零自动计算；历史会话可手动补做精修/纪要并落盘。
4. 精修稿窗口可把「说话人N」改为真实姓名，保存后全量稿与纪要即时更新且持久。
5. 设置可切换本地 whisper / FunASR 系 / 远程 ASR。
6. 完整流程：开始 → 双路实时（两级）+ 翻译 → 停止 → 会话归档 → 手动精修 →
   改名 → 生成纪要（两源各一次）→ 导出 minutes.md。
7. 实时运行态内存 < 4GB。
