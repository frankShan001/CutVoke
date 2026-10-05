# 本地自动字幕

自动字幕需要可选的 `faster-whisper` 与 OpenCC 依赖。开发环境可在项目根目录安装：

```powershell
uv pip install --python .\.venv\Scripts\python.exe -e ".[asr]"
```

启动编辑器，打开工程，在时间线选中有声视频或音频片段，再到右侧“字幕 → 自动字幕”选择语言和模型并开始识别。首次使用某个模型时需要下载模型文件；`CUTVOKE_ASR_MODEL_DIR` 可指定模型缓存目录。Tiny 适合快速校验，默认 Base；较大模型通常占用更多内存和时间。

识别设备默认使用 `auto`：检测到 CTranslate2 CUDA 设备时用 GPU float16；GPU 模型初始化失败会自动回退到 CPU int8。可在启动服务前设置 `CUTVOKE_ASR_DEVICE=cpu` 强制 CPU，或设为 `cuda` 强制使用 CUDA；无 CUDA 时后者会明确报错。字幕面板显示当前设备策略，识别完成后显示本次实际设备与计算类型。设备选择仅改变推理运行位置，不会上传素材。

## 预下载模型并离线使用

联网机器可先把模型下载并验证到单独目录。`--model` 可重复传入多个尺寸；不指定时默认准备 Base。首次下载需要联网，后续重复运行会复用同一目录中的模型缓存：

```powershell
uv run python scripts/prepare_asr_models.py `
  --model tiny --model base `
  --output 'D:\CutVoke-ASR-Models'
```

把该目录复制到目标电脑后，在启动 CutVoke 的同一终端设置缓存位置；无网络环境也可设置 Hugging Face 离线变量，确保不会尝试连接下载：

```powershell
$env:CUTVOKE_ASR_MODEL_DIR = 'D:\CutVoke-ASR-Models'
$env:HF_HUB_OFFLINE = '1'
uv run cutvoke serve
```

命令会实际加载每个请求模型进行校验，成功后输出目标目录和运行时环境变量。模型文件体积较大，复制整个目录并保留其子目录结构；Python 可选依赖 `cutvoke[asr]` 仍需在目标电脑安装。GPU 自动检测与回退依赖目标机安装的 NVIDIA 驱动及 CTranslate2 CUDA 运行库；若目标机不具备这些条件，`auto` 会使用 CPU。

识别结果先作为草稿显示，不会直接改工程。可修改每条文字及起止时间、搜索或删除候选；确认后点击“添加到工程”，所有候选以一次可撤销命令写入。切换面板或刷新页面时，当前浏览器会话保留草稿；点击“放弃识别结果”会清除。工程源文件、片段位置、裁切范围或速度变化后，旧草稿必须重新识别，以免字幕错位。已添加的字幕仍可逐条编辑并导出 SRT，MP4 导出也会烧录字幕。

识别在服务进程内排队，最多同时接收两个任务。取消会立即停止提交结果，但模型加载或解码中的底层调用可能要等到下一个进度检查点才结束；重启服务会丢失未提交的识别任务。模型下载失败、无音轨或源文件丢失会在面板提示原因。自动识别仍需要人工校对，尤其是人名、方言和背景音乐较强的素材。

供外部 Agent 使用的 `caption.transcribe` 只返回识别提案，不改工程；`caption.bulkAdd` 在源签名有效时一次提交多条字幕。原有 `caption.autoSegment` 仅按静音位置切段并生成占位文字，不属于语音识别。

## 中文识别的简体规范化

中文 ASR 识别完成后，根据检测到的语言使用 OpenCC `tw2s` 统一字形为简体；英文等其它语言不做转换。逐词时间戳保持原有时间值，词文本按整段词序列做上下文转换，避免“沿著”一类单字单独转换时丢失语境。`cutvoke[asr]` 安装项包含 OpenCC。

本地端到端验收脚本为 `scripts/verify_local_asr_acceptance.py`。2026-09-24 使用 Windows SAPI 中文语音合成生成的 12.326 秒语音视频进行检查：base 模型检测语言 `zh`，返回 2 段与 37 个逐词时码；忽略标点后与合成文本的字符错误率为 0。字幕经 `caption.bulkAdd` 保存到 SQLite，关闭并重开后仍存在；撤销后为 0 条，重做恢复 2 条；字幕烧录 MP4 含视频与音轨，完整解码退出码为 0，2 秒成片帧可见字幕。结果、工程快照及成片见 `output/asr_acceptance/real-speech-20260924-tw2s-verified/`。

该语音是合成声，画面为测试图案；这证明本地识别及工程编辑/导出链路可运行，不代表自然人声、口音或嘈杂场景的准确率。另对用户提供的 6 分钟剪映录屏运行了自动语言检测：文件虽然带 AAC 音轨，但平均音量与峰值均为 −91 dBFS，base 模型返回 0 段；验收脚本记录 `no_speech_detected`，且不计算准确率。Web 面板已有空结果说明“未识别到可用语音，可检查原声或选择其它模型后重试”。因此自然人声测试仍未满足。Tiny 模型预下载目录已在 2026-09-25 用 `HF_HUB_OFFLINE=1` 完成整条流程验收；长素材识别性能和正式安装器集成仍需验收。可用如下命令复跑（`--model-dir` 指向本机已有 Faster-Whisper 模型缓存）：

```powershell
.venv/Scripts/python.exe scripts/verify_local_asr_acceptance.py `
  output/asr_acceptance/real-speech-20260924/speech_source.mp4 `
  output/asr_acceptance/real-speech-20260924-tw2s-verified `
  --model-dir 'C:\Users\frank\AppData\Local\cutvoke\asr-models'
```

## 2026-09-27：完整真实采访与设备回退实测

本地新增 `CUTVOKE_ASR_DEVICE=auto|cpu|cuda`。默认 `auto` 会在 CTranslate2 报告 CUDA 设备时尝试 GPU float16；如果模型初始化或推理失败，会从头改用 CPU int8。`cpu` 可强制 CPU，`cuda` 则在不可用时直接报错。字幕面板显示设备策略与本次实际设备，任务结果记录设备和计算类型。自动回退、无设备、错误配置均有测试覆盖。

验收使用 Wikimedia Commons 的完整英语访谈（时长 295.644 秒，CC BY 3.0），实际推理输入约 295.644 秒，含印度英语口音和双人对话。当前代码显式使用 Base CPU int8 用时 16.027 秒，对 105 条社区字幕的暂定 WER/CER 为 78.41%/60.38%；Medium CPU int8 用时 99.208 秒，WER/CER 为 66.90%/51.28%。`auto` 在本机检测到 CUDA 后尝试 GPU，但推理环境缺少 `cublas64_12.dll`，随即回退 CPU；Medium 完整处理用时 105.478 秒，暂定 WER/CER 同为 66.90%/51.28%。计算方法见 `scripts/score_asr_reference.py`。

逐字稿来自 Commons 社区字幕，未由人工完整听校；因此这些高错误率只说明本机长素材与模型吞吐现状，不是人工真值准确率验收。当前 Base 报告、早期 Medium 对照及自动回退记录见 `output/acceptance/jy-r13-asr-full-interview-base-cpu-current-20260927/`、`output/acceptance/jy-r13-asr-full-interview-perf-20260927/` 与 `output/acceptance/jy-r13-asr-full-interview-auto-fallback-20260927/`。这次长样本只跑识别与打分，不计为保存、撤销或导出验收；JY-R13 仍部分实现，人工参考稿与目标采访材料仍待外部校对。
