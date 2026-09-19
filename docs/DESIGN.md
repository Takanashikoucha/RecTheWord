# RecTheWord v2 设计文档

> 本文档描述完全重构版（`rtw/` 包）的设计。早期基于 LiveTranslate 的 `app/` 实现
> 已从仓库移除（git 历史可追溯）。

## 1. 目标

Windows / Linux 纯 CPU（无 GPU）桌面应用，面向**会议场景**的低延迟实时处理：

1. **双通道实时识别 + 翻译**：麦克风（WASAPI 输入）+ 扬声器（WASAPI 环回）各自
   独立 VAD + ASR；中/日/英自动语种检测（韩/德可扩展）。
2. **实时字幕**：VAD 切句 → 整句解码 → 上屏，麦/扬彩色徽标分列。
3. **实时翻译**：仅走 OpenAI 兼容 API（SSE 流式）；API 不可用时显式降级。
4. **说话人粗分**：实时零算力启发式；会后 pyannote 精修为可选模块。
5. **会话存储 + AI 会议纪要**：逐句落盘，纪要走 API 流式生成 Markdown。
6. **等待态机制**：任何异步等待都显式告知用户（加载/进度/ETA）。

## 2. 关键技术结论（实测）

- **识别引擎**：Qwen3-ASR 0.6B（Apache-2.0）。
  - **路线 A（VAD 切句 + 整句解码）**：✅ 选用。24 核 RTF 0.18(zh)/0.26(en)，
    识别近乎完美，语种自动检测正确，模型加载 1.9s。
  - **路线 B（qfuxa 因果流式塔）**：❌ 弃用。该塔是英文专用微调（LibriSpeech
    蒸馏），中文输出乱码（WHK 源码警告 zh CER 11.4→85.7），24 核 RTF 0.82 会
    落后于实时。代码保留，将来有多语言塔可换回。
- **int8 量化**：torch 动态量化（308 个 Linear 层）**无加速收益（1.02x）**——
  weight-only qint8 对 transformer decoder 在 CPU 上无效（瓶颈在 attention / 全序列
  matmul，无 kernel 融合），且 `quantize_dynamic` 已是 legacy API。有效提速需走
  ONNX Runtime 的 int8 激活+权重量化（proper 融合），列为后续优化项。
- **模型来源**：仅 ModelScope（ModelManager，断点续传 + 进度回报）。唯一破例：
  qfuxa 流式塔来自 HuggingFace（用户批准，已记录）。

## 3. 架构

```
PySide6 UI（主线程，纯渲染）
   │ EventBus（QTimer 33ms 泵驱动）
   ▼
main.py                       # 环境检查 → 模型就位 → UI
rtw/
  ├─ core/
  │   ├─ events.py            # EventBus（publish/subscribe/pump）
  │   ├─ status_machine.py    # StatusMachine（begin/update/finish/error → "status"）
  │   ├─ config.py            # Config（config.yaml + 默认值合并）
  │   └─ model_manager.py     # ModelManager（ModelScope 下载，断点续传）
  ├─ audio/
  │   ├─ ring_buffer.py       # RingBuffer（30s 环形缓冲）
  │   ├─ replay_source.py     # ReplaySource（WASAPI 抽象 / 测试注入）
  │   ├─ devices.py           # DeviceManager + WasapiSource（Windows WASAPI 输入/环回）
  │   └─ linux_capture.py     # LinuxCapture + LinuxDeviceManager（PW·Pulse monitor + ALSA mic）
  ├─ vad/silero.py           # SileroVad（ONNX，32ms 窗，流式判停 + 强制断句 + 运行时可调阈值）
  ├─ asr/worker.py           # AsrWorker 子进程（spawn + Pipe IPC，Qwen3 整句解码）
  ├─ llm_api/client.py       # LlmApiClient（OpenAI 兼容，SSE 流式 + _EnvProxyFix）
  ├─ pipeline/orchestrator.py # Pipeline（VAD→ASR→翻译 + 会话 + 说话人 + 纪要编排）
  └─ pipeline/backlog_guard.py # BacklogGuard（积压自适应：L1 源头减量 + L2 drop-oldest）
  ├─ session/store.py        # SessionStore
  ├─ diarize/coarse.py       # CoarseDiarizer（实时粗分）
  ├─ minutes/generator.py    # MinutesGenerator
  └─ ui/
      ├─ theme.py            # QSS 主题（OKLCH 调校）
      ├─ splash.py           # SplashWindow（5 阶段进度）
      ├─ main_window.py      # MainWindow（顶栏/左面板/字幕舞台/dock/toast）
      ├─ overlay.py          # OverlayWindow（透明浮窗，4 主题 + 拖拽）
      └─ app.py              # run_app（装配 + EventBus 泵）
```

### 数据流（运行时）

```
采集源（Windows=WASAPI / Linux=PW·Pulse monitor + ALSA / 测试=ReplaySource）
  ├─ sys → RingBuffer → SileroVad ─┐
  └─ mic → RingBuffer → SileroVad ─┴→ 各自 seg_q（BacklogGuard 监控深度）
        ├─ _lane_loop：drain ring → VAD 切句 → seg_q（publish "seg"）
        ├─ _asr_worker_loop：seg → AsrWorker.transcribe（整句）
        │     → publish "asr"（含 t_first_ms / speaker）
        │     → 段 RMS → CoarseDiarizer.assign（说话人槽）
        │     → SessionStore.add_line（落盘）
        │     → _translate（fire-and-forget，不阻塞 worker）
        └─ LlmApiClient.translate_stream（SSE 逐字）→ publish "trans"
UI（订阅 EventBus）：
  status → toast / splash 进度
  asr    → 主窗口 + 浮窗 add_line（麦/扬徽标 + 原文）
  trans  → 译文增量回填
  trans_err / asr_err → 显式降级提示
停止时：
  冲刷 → finalize_translations（译文补写）→ SessionStore.close() 归档
```

### 进程隔离

ASR 推理在独立子进程（`multiprocessing` spawn + Pipe IPC + 超时），与 UI 进程零
GIL 竞争。UI 进程纯渲染，所有数据经 EventBus 到达（QTimer 33ms 泵驱动）。

## 4. 关键设计决策

### 4.1 路线 A：VAD 切句 + 整句解码
不做逐 chunk 增量解码（路线 B 的流式塔中文不可用），而是 VAD 切出完整句 →
一次性整句解码。牺牲一点首字延迟换取识别质量与工程简洁。切句策略可调
（`segment_policy`：balanced 3s / aggressive 1.5s）。

### 4.2 翻译 fire-and-forget
`_translate` 不阻塞 ASR worker（早期 `done_evt.wait()` 会让后续段堆积）。译文
增量经 SSE 回调异步回填 UI，`_done` 时补写会话存储。

### 4.3 等待态机制（用户约束 ⑤⑥）
每个异步等待（env_check / vad_load / asr_load / api_health）都经
`StatusMachine` 广播 `working → done/error`（含 message / eta_s），UI 的 toast 与
splash 进度条据此显式告知用户，杜绝无声等待。

### 4.4 降级路径（用户约束）
翻译 / 纪要**仅走 API**。API 不可达时：ASR 照常出字幕，翻译/纪要**显式报错**
（"翻译 API 不可用，仅显示原文" / Connection refused），不静默失败。

### 4.5 跨平台音频采集（Windows / Linux 对称）
两平台提供同形接口（`start/stop/join/switch` + 往 RingBuffer 写 PCM16 16kHz mono），
`orchestrator` 按 `audio.source` 分派，上层零感知：
- **Windows**：`devices.py`（WASAPI 输入 + 官方环回虚拟设备）。
- **Linux**：`linux_capture.py`（mic 走 `arecord`/ALSA；sys 环回走 PulseAudio/PipeWire
  的 **sink monitor source**——Linux 下 WASAPI 环回的等价物）。后端自动探测
  PipeWire → PulseAudio → 纯 ALSA。
- 非对应平台优雅降级：`enumerate` 返回空列表，链路可走 `replay` 注入做端到端测试。
- `audio.source` 缺省时**平台自适应**（Windows→wasapi / Linux→alsa）。

### 4.6 积压自适应恢复（两级，防 ASR 无限堆积）
ASR 处理跟不上 VAD 产段速度时，队列会无限堆积 → 实时性崩坏 + 性能下降。
`BacklogGuard` 两级应对（`backlog.*` 配置）：
- **L1 源头减量**：队列深度超 `high_watermark` → 动态调 VAD 双参，**少产段、少喂 ASR**
  （治本，而非事后截断）：阈值抬高（`boost_threshold` 0.5→0.62，更不敏感 → 更少起句）
  + 最短静音门限缩短（`boost_min_silence_ms` 800→500ms，更早断句 → 段更短更快喂完）。
- **L2 drop-oldest 兜底**：L1 后仍超 `drop_watermark` → 丢弃最旧段、保最新实时段。
- **有界兜底**：`seg_q` 硬上限 `maxsize`，绝不撑爆内存。
- **恢复防抖**：回落到 `low_watermark` 且维持 `recover_grace_s` 才宣告恢复，
  避免阈值来回抖动。状态机 NORMAL/BACKLOGGED/RECOVERED，经 `"backlog"` 事件广播。

## 5. 边缘情况与失败模式

- **环回不可用** → 降级仅 mic 单路；纪要仍可用文字流源。
- **模型首跑下载** → ModelManager 断点续传 + 进度回报 splash；`MODELSCOPE_CACHE`
  可预置。
- **API 端点不可达** → 降级仅原文 + 显式提示（见 4.4）。
- **CPU 过载 / ASR 积压** → 两路共享单 ASR worker 串行推理；突发负载会排队，
  由 `BacklogGuard` 两级自适应缓解（L1 源头减量 + L2 drop-oldest，见 4.6），
  防无限堆积。
- **Linux 音频后端缺失**（无 arecord / 无 Pulse·PipeWire）→ `install.sh` 给出
  安装提示；`probe_backend` 自动探测回落；极端缺失时该通道降级，另一通道仍可用。
- **会话目录损坏/缺文件** → 仍可打开现存部分。
- **httpx no_proxy 含 `::1,[::1]`** → httpx 0.28 解析崩溃，`_EnvProxyFix` 上下文
  临时归一化（已修）。

## 6. 内存预算（16G 机器）

| 组件 | 估算 |
|---|---|
| PySide6 + 应用 | ~0.5GB |
| Qwen3-ASR 0.6B（fp32，单实例，两路共享） | ~1.5GB |
| Silero VAD ×2（ONNX） | ~0.2GB |
| WASAPI 采集 | ~0.2GB |
| **实时运行态合计** | **~2.5GB** |
| 2h 长跑估算（稳态增量 + 每句边际） | <500MB 增长 |

## 7. 假设

- Windows 10/11 x64 **或 Linux（Debian/Ubuntu/Fedora 等，需 PipeWire 或 PulseAudio）**，
  Python 3.10–3.12，首跑有网。
- 实时 = 整句解码 1-3 秒出文本；翻译只跟 final（省 token、防抖动）。
- 实时界面「麦克风 / 扬声器」两路；说话人粗分实时做，精修可选。
- 停止后会话归档；纪要为用户手动触发。

## 8. 验收标准

1. 全链路自测全绿（`test_e2e_full`：VAD→ASR→翻译）。
2. 双路径（`test_p4_e2e`）：API 在线 / 宕机，后者 ASR 照常 + 翻译/纪要显式报错。
3. 1x 实时（`test_realtime_1x`）：83s 双通道长跑 0 错误，双通道均出声。
4. 硬指标（`test_acceptance`）：冷启动 <15s、首字 p50<1.0/p95<1.5、2h 内存 <500MB。
5. UI（`test_ui_smoke`）：三窗口实例化 + 事件注入 + 截图验证。
6. 积压自适应（`test_backlog_adapt`）：两级水位 / drop-oldest 保最新 / 有界 /
   grace 状态机 / reset，7 项全绿。
7. 完整流程：启动 → 双通道实时 + 翻译 → 停止 → 会话归档 → 生成纪要 → 导出。
