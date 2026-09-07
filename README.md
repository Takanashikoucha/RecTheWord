# RecTheWord

实时多语转录（中 / 英 / 日）+ 说话人分离 + 实时中文翻译 + AI 会议纪要。
基于 **FFmpeg（WASAPI 共享采集）** + **FunASR（流式 + 离线分离）** + **PySide6**，
面向 **Windows CPU（无 GPU）** 的低延迟场景。

---

## 功能

| 功能 | 说明 |
|---|---|
| **设备枚举** | 用 FFmpeg `dshow` 列出全部麦克风与扬声器，界面各选一个。 |
| **共享采集** | FFmpeg `wasapi -shared` 抓取麦克风 + 扬声器回环（`loopback:`），不独占设备。 |
| **实时字幕（分路）** | 麦克风流与扬声器流**各自独立**流式识别（`paraformer-zh-streaming`），分别以 `🎤 麦克风` / `🔊 扬声器` 标注显示，支持中/英/日。 |
| **实时翻译** | 可选配置外部 **OpenAI 兼容接口**，把识别文字按句节流翻译成简体中文并显示。 |
| **离线说话人分离** | 结束录制后，对混合录音跑 **SenseVoice + FSMN-VAD + CT-PUNC + CAM++**，得到**按说话人分开、带时间戳**的全量稿。 |
| **说话人标注** | 把匿名 `说话人0/1/…` 映射为**真实姓名**（可编辑、持久化），即时应用到全量稿与纪要。 |
| **会议纪要** | 把带姓名+时间戳的全量稿交给 AI，生成结构化中文纪要（议题 / 讨论要点 / 结论待办）。 |

---

## 系统要求

- **Windows 10 / 11 x64**
- **Python 3.10 – 3.12**（PATH 中可用）
- 网络（首次运行下载 ASR 模型与 FFmpeg 二进制）
- CPU 即可运行（无 GPU 要求）

---

## 安装与运行

```bat
:: 一次性安装（装依赖 + 下载 FFmpeg）
scripts\install_windows.bat

:: 启动
scripts\run.bat
:: 或
python main.py
```

首次点击“开始”会**自动下载 ASR 模型**（几十到上百 MB，取决于所选模型），
请耐心等待状态栏提示“模型加载完成”。

### 手动安装（如一键脚本失败）

```bat
pip install -r requirements.txt
python scripts\download_ffmpeg_windows.py
```

---

## 使用流程

1. 启动后界面自动枚举设备。**麦克风**下拉选输入设备，**扬声器**下拉选回环设备。
   （若扬声器列表为空，可只录麦克风；扬声器回环用于捕获“对方/系统播放的声音”。）
2. 点 **⚙ 设置**：
   - 配置外部 AI（Base URL / API Key / 模型 / 温度）以启用实时翻译与会议纪要；
   - 可选改 ASR 模型 / hub（ModelScope 或 HuggingFace）/ CPU 线程数 / 本地模型目录。
3. 点 **● 开始**：实时字幕与翻译开始滚动。
4. 点 **■ 停止**：
   - 冲刷识别缓存；
   - 离线做说话人分离（耗时数十秒到数分钟，取决于录音长度与 CPU）；
   - 下方显示**分说话人全量稿**与（若配置 AI）**会议纪要**。
5. 在**全量稿**中可把说话人改名为真实姓名（自动保存到配置，下次复用）。

> 说明：实时路径为 CPU 低延迟设计，**实时字幕按“麦 vs 扬”两路区分**，不做逐句精确到每个
> 人的分离；**逐说话人精确分离 + 真实姓名标注 + 纪要在“停止”时离线完成**。

---

## 目录结构

```
main.py                     # 入口
rectheword/
  audio/   devices.py  capture.py     # 设备枚举 + WASAPI 双路采集/混合
  asr/     engine.py                  # 流式 + 离线（分离）引擎
  pipeline/ dual_pipeline.py diarize.py  # 双声道拆分/切块 + 离线分离
  ai/      client.py translate.py minutes.py  # OpenAI 兼容客户端 / 翻译 / 纪要
  labels.py                         # 说话人姓名标注
  config.py                         # 配置持久化
  ui/      main_window.py settings_dialog.py  # PySide6 界面
scripts/    download_ffmpeg_windows.py install_windows.bat run.bat
tests/      纯逻辑单测（无需 ML / Qt 运行时）
docs/       DESIGN.md  PROGRESS.md
models/ recordings/ vendor/ffmpeg/    # 运行期产物（不入库，已 gitignore）
```

---

## 配置（`~/.rectheword/config.json`，不入库）

保存：所选设备、AI 端点/Key/模型、ASR 模型与 hub、CPU 线程、本地模型目录、
**说话人姓名映射**（跨会话复用）。

---

## 调试（Windows）

- **枚举设备**：`python -m rectheword.audio.devices`
- **FFmpeg 是否可用**：`ffmpeg -version`
- **手动试采**（验证共享/回环）：
  ```bat
  ffmpeg -f wasapi -shared 1 -sample_rate 16000 -i "麦克风名" -f wasapi -shared 1 -sample_rate 16000 -i "loopback:扬声器名" -filter_complex "[0:a]aformat=sample_rates=16000:channel_layouts=mono[m];[1:a]aformat=sample_rates=16000:channel_layouts=mono[s];[m][s]join=inputs=2:channel_layouts=stereo[o]" -map "[o]" -f f32le test.raw
  ```
- 状态栏会回显 ffmpeg `stderr` 尾部，常见报错见下。

### 常见报错

| 现象 | 处理 |
|---|---|
| `Device or resource busy` / 独占 | 确认用**共享模式**（本应用默认）；关掉独占该设备的全屏/独占程序。 |
| 扬声器回环无声音 / 设备找不到 | 确认所选是**渲染（输出）设备**；部分设备不支持 loopback，换一个扬声器试试。 |
| 模型下载慢/失败 | 在“设置”把 hub 切到 `hf`（HuggingFace）；或预先用 `model_dir` 指定已下载目录。 |
| 实时延迟高 | 降低“CPU 线程数”或减少同开的其他程序；日语实时质量受流式模型限制（离线稿更好）。 |
| 未识别到内容 | 检查麦克风选择、音量；纯静音不会出字。 |

---

## 测试

纯逻辑单测（无需安装 torch/funasr，但需要 `numpy`）：

```bat
pip install pytest numpy
python -m pytest tests/ -q
```

---

## 许可

软件代码 MIT；模型权重许可见各自模型卡（FunASR / ModelScope / HuggingFace）。
