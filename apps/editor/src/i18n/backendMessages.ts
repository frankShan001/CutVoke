import { tr } from ".";

/** Only fixed service-owned capability/phase messages belong here.
 * Never use this adapter on project content, filenames or free-form diagnostics. */
export function backendMessage(text: string): string {
  const tracking = text.match(/^SAM2 跨帧跟踪（([^）]+)）$/);
  if (tracking) return tr("SAM2 跨帧跟踪（{0}）", tracking[1]);
  switch (text) {
    case "自动识别待配置：请安装 cutvoke[asr]。": return tr("自动识别待配置：请安装 cutvoke[asr]。");
    case "中文识别规范化组件待配置：请重新安装 cutvoke[asr]。": return tr("中文识别规范化组件待配置：请重新安装 cutvoke[asr]。");
    case "CUTVOKE_ASR_DEVICE 只支持 auto、cpu 或 cuda。": return tr("CUTVOKE_ASR_DEVICE 只支持 auto、cpu 或 cuda。");
    case "首次识别会下载所选模型；也可设置 CUTVOKE_ASR_MODEL_DIR 指定模型缓存。": return tr("首次识别会下载所选模型；也可设置 CUTVOKE_ASR_MODEL_DIR 指定模型缓存。");
    case "SAM2 视频身份跟踪已就绪；保留所有人物仍需准备 MediaPipe 模型。": return tr("SAM2 视频身份跟踪已就绪；保留所有人物仍需准备 MediaPipe 模型。");
    case "人物抠像组件未安装：请安装 cutvoke[person]（含 MediaPipe 与 OpenCV）。": return tr("人物抠像组件未安装：请安装 cutvoke[person]（含 MediaPipe 与 OpenCV）。");
    case "人物抠像需要 FFmpeg 与 ffprobe；请先安装 FFmpeg 并加入 PATH。": return tr("人物抠像需要 FFmpeg 与 ffprobe；请先安装 FFmpeg 并加入 PATH。");
    case "人物分割模型未准备：运行 cutvoke-person-model。": return tr("人物分割模型未准备：运行 cutvoke-person-model。");
    case "本机人物分割已就绪（MediaPipe Selfie Segmentation，Apache-2.0）；视频只在本机处理。": return tr("本机人物分割已就绪（MediaPipe Selfie Segmentation，Apache-2.0）；视频只在本机处理。");
    case "排队中": return tr("排队中");
    case "已取消": return tr("已取消");
    case "服务已关闭": return tr("服务已关闭");
    case "准备识别": return tr("准备识别");
    case "识别语音": return tr("识别语音");
    case "识别完成": return tr("识别完成");
    case "识别失败": return tr("识别失败");
    case "识别任务不可用": return tr("识别任务不可用");
    case "提取片段音频": return tr("提取片段音频");
    case "加载识别模型": return tr("加载识别模型");
    case "GPU 初始化失败，正在回退到 CPU": return tr("GPU 初始化失败，正在回退到 CPU");
    case "GPU 推理失败，正在回退到 CPU": return tr("GPU 推理失败，正在回退到 CPU");
    case "正在应用到时间线": return tr("正在应用到时间线");
    case "已生成透明片段，可重试应用": return tr("已生成透明片段，可重试应用");
    case "已应用；可撤销恢复原片": return tr("已应用；可撤销恢复原片");
    case "准备人物分割": return tr("准备人物分割");
    case "透明片段已生成": return tr("透明片段已生成");
    case "抠像失败": return tr("抠像失败");
    case "正在准备 SAM2 跟踪帧": return tr("正在准备 SAM2 跟踪帧");
    case "正在载入人物分割模型": return tr("正在载入人物分割模型");
    case "正在编码 SAM2 抠像视频": return tr("正在编码 SAM2 抠像视频");
    case "正在逐帧分割人物": return tr("正在逐帧分割人物");
    case "正在保留源音频并封装透明视频": return tr("正在保留源音频并封装透明视频");
    case "人物抠像完成": return tr("人物抠像完成");
    default: return tr("服务提示（原文）：{0}", text);
  }
}
