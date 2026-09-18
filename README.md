# RecTheWord v2

**会议场景的双通道实时字幕 + 实时翻译 + AI 会议纪要**桌面应用（Windows 纯 CPU，无 GPU）。

完全重构版（`rtw/` 包），替换早期基于 LiveTranslate 的 `app/` 实现。核心识别引擎为
**Qwen3-ASR 0.6B**（Apache-2.0），翻译与纪要**仅走 OpenAI 兼容 API**，模型**仅从
ModelScope 下载**（唯一破例：qfuxa 流式塔来自 HuggingFace，经用户批准）。

## 能力一览

| 能力 | 说明 | 验证状态 |
|---|---|---|
| **双通道实时识别** | 麦克风（WASAPI 输入）+ 扬声器（WASAPI 环回）各自独立 VAD + ASR | ✅ 全链路 |
| **实时字幕** | VAD 切句 → 整句解码 → 上屏；麦/扬彩色徽标分列 | ✅ 全链路 |
| **实时翻译** | 走 OpenAI 兼容 API，SSE 流式逐字回填；API 不可用时显式降级（只显示原文 + 提示） | ✅ 全链路（含降级路径） |
| **多语种** | 中 / 日 / 英自动语种检测（韩 / 德可扩展） | ✅ 自动检测 |
| **说话人粗分** | 实时按通道 + 能量/停顿启发式（零额外算力）；会后 pyannote 精修为可选模块 | ✅ 落盘 |
| **会话存储** | 每场会议 = 一个目录（meta / transcript.jsonl / minutes.md） | ✅ 落盘 |
| **AI 会议纪要** | 走 API 流式生成 Markdown（摘要 / 决议 / 待办），自动落盘 | ✅ 全链路（含降级路径） |
| **UI** | splash 启动页 + 主窗口 + 透明字幕浮窗（PySide6，4 主题 + 拖拽） | ✅ offscreen 冒烟 |
| **等待态机制** | 任何异步等待（模型加载 / API 检查）都经 StatusMachine 显式告知用户 | ✅ 事件齐全 |

## 架构

```
main.py                  # 顶层入口（环境检查 → 模型就位 → UI）
rtw/                     # 全部应用模块
  ├─ core/               # EventBus / StatusMachine / Config / ModelManager(ModelScope)
  ├─ audio/             # RingBuffer / ReplaySource(WASAPI 抽象)
  ├─ vad/               # SileroVad(ONNX)
  ├─ asr/               # AsrWorker 子进程（Qwen3-ASR 整句解码，与 UI 零竞争）
  ├─ llm_api/           # LlmApiClient（OpenAI 兼容，SSE 流式 + 故障注入）
  ├─ pipeline/          # orchestrator（VAD→ASR→翻译 + 会话 + 说话人 + 纪要编排）
  ├─ session/           # SessionStore
  ├─ diarize/           # CoarseDiarizer（实时粗分）
  ├─ minutes/           # MinutesGenerator
  └─ ui/                # theme / splash / main_window / overlay / app
tests/                   # 单元 + 全链路 + 双路径 + 1x 实时 + 验收 + int8 基准
config.yaml              # 基础默认（锚定仓库根）
install.ps1 / start.bat  # uv 加速安装 + 环境缺失自动衔接
docs/DESIGN.md           # 设计方案
mockup/                  # UI 概念稿（HTML）
```

**进程隔离**：ASR 推理在独立子进程（spawn），与 UI 进程零 GIL 竞争；UI 进程纯渲染，
所有数据经 EventBus 到达。

## 选型决策（实测）

- **路线 A（VAD 切句 + 整句解码）**：✅ 选用。24 核 RTF 0.18(zh)/0.26(en)，识别近乎完美，
  语种自动检测正确，模型加载 1.9s。
- **路线 B（qfuxa 因果流式塔）**：❌ 弃用。该塔是英文专用微调（LibriSpeech 蒸馏），
  中文输出乱码（WHK 源码亦警告 zh CER 11.4→85.7），24 核 RTF 0.82 会落后于实时。
  代码保留，将来有多语言塔可换回。

## 系统要求

- **Windows 10 / 11 x64**
- **Python 3.10 – 3.12**
- CPU 即可（无 GPU 要求）
- 网络（首次运行从 ModelScope 下载模型）

## 安装与运行

```bat
:: 一键启动（环境缺失时自动调 install.ps1：uv 建 venv + 装依赖 + ModelScope 预取模型）
start.bat
```

手动安装：

```bat
python -m venv .venv
.venv\Scripts\activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

> 模型不内嵌 git（GitHub 单文件上限 100MB），安装时从 ModelScope 拉取（ModelManager，
> 断点续传 + 进度回报给 splash）。唯一破例的 qfuxa 流式塔来自 HuggingFace（用户批准）。

## 性能（24 核开发机基线，8 核目标机按比例外推）

| 指标 | 24 核实测 | 8 核 ≤3GHz 预估 | 目标 |
|---|---|---|---|
| 冷启动到 ASR 就绪 | 3.7s | ~8-12s | <15s ✅ |
| ASR RTF | 0.18 / 0.26 | ~0.5-0.9 | <1（实时） |
| 首字更新 p50 / p95 | 0.54s / 0.81s | ~1.5-2.5s | p50<1.0 / p95<1.5 |
| 2h 内存估算 | 459MB | 相近 | <500MB ✅ |

**int8 量化结论**：torch 动态量化（308 个 Linear 层）**无加速收益（1.02x）**——weight-only
qint8 对 transformer decoder 在 CPU 上无效（瓶颈在 attention / 全序列 matmul，无 kernel
融合），且 `quantize_dynamic` 已是 legacy API。**有效的 int8 提速需走 ONNX Runtime 的
int8 激活+权重量化**（proper 融合），列为后续优化项。

## 验证矩阵

| 测试 | 覆盖 | 结果 |
|---|---|---|
| `test_e2e_full` | VAD→ASR→翻译全链路（16x 回放） | ✅ 5 段全通 0 错误 |
| `test_p4_e2e` | 会话存储 + 说话人 + 纪要（正常 / API 宕机双路径） | ✅ 双路径 |
| `test_realtime_1x` | 83s 双通道 TTS 对话，**1x 不加速** | ✅ 双通道 0 错误 |
| `test_acceptance` | 冷启动 / 延迟 / 内存 硬指标 | ✅ PASS |
| `test_int8_bench` | int8 vs fp32 性能 + 识别一致性 | ⚠️ 无加速（见上） |
| `test_ui_smoke` | 三窗口实例化 + 事件注入 + 截图 | ✅ offscreen |

> 注：1x 实时测试的识别率（~70%）受 espeak-ng 机器人腔音质限制；干净短音频离线基准为
> 4/5 准确。真实人声会议预期显著更高。

## 配置

- `config.yaml`：基础默认（音频设备、VAD 参数、ASR 引擎/切句策略、翻译 API、字幕样式）。
  - `asr.segment_policy`：`balanced`(3s，默认，稳) / `aggressive`(1.5s，低首字延迟但对长句易碎片化)
  - `vad.min_silence_ms`：800（实测校准，TTS/噪声音频音节间隙较长）
- 翻译 / 纪要 API：任意 OpenAI 兼容端点（`api.base_url` + `api_key` + `model`）。

## 会话存储布局

```
~/.rectheword/sessions/<YYYYMMDD-HHMMSS>/
  meta.json        # 引擎 / 目标语种 / 创建时间
  transcript.jsonl # 逐句 {时间戳, 通道, 原文, 语种, 说话人, 译文}
  minutes.md       # （生成后）Markdown 纪要
```

## 许可

软件代码 MIT；Qwen3-ASR / qwen-asr / qwen3-asr-causal 均 Apache-2.0；PySide6 GPL
（内部工具可）；Silero / ONNX MIT/Apache；pyannote MIT。模型权重许可见各自模型卡。
