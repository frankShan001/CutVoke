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
uv run python tools/dev/prepare_asr_models.py `
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

## 验证自己的环境

可使用独立视频和新输出目录检查识别、字幕保存/撤销及烧录导出：

```powershell
python tools/verify/verify_local_asr_acceptance.py `
  'D:\Samples\speech.mp4' `
  '.artifacts/asr-check' `
  --model-dir 'D:\CutVoke-ASR-Models'
```

把输入视频和模型目录替换为自己的路径。识别耗时与错误率取决于模型、设备、
语言、口音和背景声音；合成语音的结果不能代表自然人声准确率。
使用 `tools/verify/score_asr_reference.py --help` 查看人工参考稿的评分方式。
日志、参考稿和生成视频保留在本地输出目录，不提交到源码仓库。
