# 音频工作台

编辑器右侧的“音频分析”页签包含配音录制、混音台、音源分离与响度/节拍分析。

## 配音

点击“选择录音设备”授权麦克风并选择输入设备。可选择 0、3、5 秒倒计时，默认随画面播放录音：等待预览真正播放后才开始采集，预览暂停或缓冲时暂停采集，播放恢复后继续。录音以实际开始时的工程时间定位；到工程末尾自动停止。空工程可关闭“随画面播放录音”后录制。

“实时监听麦克风”默认关闭；打开后可调整监听音量，建议使用耳机避免扬声器回授。录音停止后可试听或重录，确认后“添加配音到时间线”会将真实 PCM WAV 上传到受管理素材库，在新音频轨道插入，作为一次可撤销编辑。单次最长 5 分钟，浏览器在停止时解码并转换 WAV，长录音会占用与采样数相应的内存。离开面板或切换工程会停止采集并释放设备；尚未添加的试听稿保存在本次面板会话中。

浏览器录音要求支持 `getUserMedia` 和 `MediaRecorder`，并在 localhost、安全 HTTPS 页面或桌面客户端中运行。权限被拒绝、设备断开或解码失败会显示真实错误，不会生成替代测试音。

Agent 的 `audio.record` 也是真实录音：Windows 使用 FFmpeg DirectShow；macOS 使用 AVFoundation；Linux 默认 PulseAudio，设备参数可用 `alsa:hw:1,0` 指定 ALSA。FFmpeg 未包含对应采集后端、权限或设备不可用时明确失败。该命令仅输出文件，不自动写时间线。

## 混音

选择轨道、子混音总线或主输出，可设置增益（-96 至 +24 dB）、声像、静音和独奏。音频和带原声的视频轨均可路由到一个子混音总线；未指定总线时直接进入主输出。总线汇入主输出，不支持总线之间循环路由。删除总线会把所属轨道返回主输出，一次撤销可恢复总线和路由。

音量包络以**工程的绝对秒数**保存，点间按 dB 线性插值，首尾维持端点值。其增益与通道静态增益、片段音量和片段关键帧相乘。通道效果链按列表顺序应用，可添加、调整、旁路、移除与重排 EQ、压缩器和响度处理。确认“应用混音”后进入工程历史；保存、重新打开、撤销/重做、连续预览和最终导出使用同一份状态。

带通道混音、总线效果或片段音量关键帧时，声音预览使用完整服务端混音，保证与导出一致。此时修改通道后会重新准备相应预览。

Agent 命令：

- `audio.busAdd`：`{"busId":"dialog","name":"对白"}`。
- `audio.mixer`：`{"trackId":"a1","mix":{"gainDb":-3,"busId":"dialog","envelope":[{"time":0,"gainDb":-12},{"time":2,"gainDb":0}]}}`。
- `audio.mixer`：用 `busId` 或 `master:true` 代替 `trackId`，调整总线或主输出。
- `audio.busRemove`：删除指定总线。

混音修改支持预演，并可放入 `edit.batch`；使用当前 revision 和既有编辑租约规则。

## 本地模型音源分离

`audio.separate` 使用真实 Demucs 神经网络输出无损 float32 WAV，可选择人声/伴奏或人声/鼓/贝斯/其他四轨。它与旧 `audio.vocalEnhance` 的中置/侧声道近似处理是不同命令。首次使用会下载模型权重；后续复用 PyTorch 缓存，素材不上传到模型服务。

开发环境安装：

```powershell
uv pip install --python .venv/Scripts/python.exe -e '.[separation]'
```

也可使用独立 Python 3.10–3.12 环境安装 Demucs 4.0.1、相互匹配的 PyTorch/torchaudio 2.0–2.5 与 soundfile，并在启动 CutVoke 前设置：

```powershell
$env:CUTVOKE_SEPARATION_PYTHON = 'D:\audio-model-env\Scripts\python.exe'
```

CUDA 运行时与驱动需和安装的 PyTorch 匹配；`device:auto` 在该模型环境可用时选择 CUDA，否则使用 CPU。显式要求 CUDA 却不可用会报错。官方模型和安装说明见 [Demucs 仓库](https://github.com/facebookresearch/demucs)。

在面板选择素材、模型和一个新的或空的输出文件夹，然后开始分离。处理完成后可把任一分轨加入时间线继续编辑。命令示例：

```json
{"audioPath":"D:/media/song.wav","outDir":"D:/media/song-stems","model":"htdemucs","device":"auto","stems":"vocals"}
```

分离是文件级分析，不会修改工程或自动覆盖已存在的输出。模型推理上限两小时，失败返回原因并清理临时输入。分离质量取决于混音与素材，须试听后使用；该功能不是零串音或无损恢复原始分轨的保证。
