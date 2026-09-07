# RecTheWord 进度 / 交接文档

> 本文档随实现推进随时追加，供另一个会话 handoff 继续。
> 最后更新：首次第一版构建完成，已推送 v0.1.0 到远端 `main`（commit `1172ea6`）。

## 环境事实（构建会话）

- 构建机：Linux（无 GUI，无 Windows 环境，**无法在本地运行验证 Windows 行为**）。
- 构建机 Python：3.14.7（仅用于 `py_compile` 语法检查与纯逻辑单测；**未安装** torch/funasr/PySide6）。
- 目标运行环境：**Windows 10/11 x64，Python 3.10–3.12，CPU（无 GPU）**。
- 目标仓库：`https://github.com/Takanashikoucha/RecTheWord`（HTTPS + PAT 推送）。

## 已完成（第一版 v0.1.0）

- [x] **Step 0 脚手架**：包结构 `rectheword/{audio,asr,ai,pipeline,ui}`、`pyproject.toml`、
      `requirements.txt`、`.gitignore`（忽略 models/recordings/vendor/config/`*.wav`）、`main.py`。
- [x] **Step 1 设备与 FFmpeg**
      - `audio/devices.py`：`find_ffmpeg`/`find_ffprobe`、`list_audio_devices`（dshow 解析）、
        `list_render_devices`（wasapi -list_devices 回退 dshow）、`wasapi_device_name`（loopback 前缀）。
      - `audio/capture.py`：`build_ffmpeg_args`（双路 wasapi 共享 + join 双声道 f32le stdout +
        amix 混合 mono wav）、`CaptureEngine`（子进程起停、stderr 排水、`read_stdout`）。
      - `scripts/download_ffmpeg_windows.py`（Gyan 主 + BtbN 镜像，解压到 `vendor/ffmpeg`）。
      - 单测：`tests/test_capture.py`（命令拼接）、`tests/test_devices.py`（dshow 解析）。
- [x] **Step 2 双路管道 + 实时 ASR**
      - `pipeline/dual_pipeline.py`：`split_stereo_f32le` + `DualAudioPipeline`（QThread，
        拆双声道→按 9600 采样切块→分路 `chunk` 信号；尾块补齐后冲刷）。
      - `asr/engine.py`：`StreamingEngine`（每流独立 cache、`feed`/`finalize`、延迟 import funasr）、
        `OfflineEngine`（SenseVoice+VAD+PUNC+CAM++，`transcribe` 读 `sentence_info`）。
      - 单测：`tests/test_pipeline.py`（双声道拆分、切块累积）。
- [x] **Step 3 离线说话人分离**：`pipeline/diarize.py`（`diarize_wav`，引擎缓存于 settings）。
- [x] **Step 4 说话人标注**：`labels.py`（`SpeakerLabels`：spk↔姓名 映射、`apply`、
      `to_minutes_text`/`to_transcript_text`、`distinct_speakers`；持久化于 config）。
      单测：`tests/test_labels.py`。
- [x] **Step 5 AI**：`ai/client.py`（OpenAI 兼容 `/chat/completions`，超时+重试、base_url 归一化）、
      `ai/translate.py`（按句节流增量翻译，异步线程，失败降级）、`ai/minutes.py`（生成纪要）。
      单测：`tests/test_config.py`（配置 round-trip、URL 归一化）。
- [x] **Step 6 UI**：`ui/main_window.py`（设备下拉、开始/停止、双路实时字幕、双路翻译、
      分说话人全量稿、纪要面板、状态机、ModelLoadWorker/StopWorker 线程、close 清理）、
      `ui/settings_dialog.py`（AI/ASR/hub/线程/模型目录/说话人前缀）、`config.py`。
- [x] **Step 7 文档/脚本**：`README.md`（安装/使用/调试/常见报错）、`docs/DESIGN.md`（设计）、
      本 `docs/PROGRESS.md`、`scripts/install_windows.bat`、`scripts/run.bat`。
- [x] **提交**：`git init` + 首个 commit 推送到目标仓库（HTTPS + PAT，token 不落盘）。

## 已知限制 / 待验证（明天 Windows 测试聚焦）

1. **dshow 扬声器枚举**：dshow 只列**捕获能力**的音频设备；渲染（扬声器）设备名靠
   `list_render_devices`（wasapi -list_devices，回退 dshow 启发式）。**需在 Windows 上确认
   扬声器下拉能列出真实渲染设备**；若为空，用户可手动输入或用麦克风-only 模式。
2. **loopback 回环**：`loopback:<扬声器名>` 前缀需在 Windows 上验证可用；部分设备不支持。
3. **模型首跑下载**：Paraformer-streaming + SenseVoice + VAD + PUNC + CAM++，数百 MB 级，需网络。
4. **两路实时 CPU 负载**：两个流式会话，CPU 约为单路 2 倍；用 ncpu/chunk_size 调节。
5. **实时仅分“麦 vs 扬”**；逐人精确分离在“停止”时离线完成（SenseVoice+CAM++）。
6. **纯日语实时**质量受 paraformer-zh-streaming 限制（离线 SenseVoice 更好）。
7. **无法在 Linux 验证**：WASAPI、dshow、设备占用、模型加载、UI 交互均需在 Windows 上验证。

## 给接手会话的提示

- 代码已通过 `python3 -m py_compile`（全部文件）与纯逻辑单测（需 `pip install numpy pytest`）。
- **未在 Linux 安装/运行** torch/funasr/PySide6（Linux 无法跑 Windows 路径）。
- 若 Windows 测试反馈问题，优先看：
  - 状态栏是否显示“未找到麦克风” → `find_ffmpeg()` / `vendor/ffmpeg` 是否存在；
  - 启动采集失败 → `CaptureEngine` 的 `error_tail()`（README 有手动试采命令）；
  - 扬声器下拉为空 → `list_render_devices` 启发式是否匹配该系统的设备名。
- 关键文件：`rectheword/audio/capture.py`（ffmpeg 命令）、`rectheword/pipeline/dual_pipeline.py`
  （拆分/切块）、`rectheword/asr/engine.py`（流式/离线模型）、`rectheword/ui/main_window.py`（接线）。

## 变更日志

- **v0.1.0**（首次）：完整第一版。FFmpeg 双路 WASAPI 共享采集 + FunASR 双路实时 +
  离线说话人分离 + 姓名标注 + 实时翻译 + AI 会议纪要 + PySide6 UI + 文档/脚本/单测。
  - 已推送到远端 `main`，commit `1172ea6`。
  - 推送方式说明：构建机网络下 `git -c http.extraheader="Authorization: Bearer <PAT>"`
    会超时/鉴权失败，改用 **URL 内嵌 token**（`https://x-access-token:<PAT>@github.com/...`）
    推送成功。token **未写入** remote URL、`.git/config` 或任何文件（仅命令行临时使用）。
  - 单测：`tests/` 共 20 个用例全部通过（需 `pip install numpy pytest`）。
