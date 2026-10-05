# MagicTouch 点提示人物抠像

## 功能

人物抠像面板的“只保留我点选的人物”模式现在提供两种跟踪方式：

- **轮廓连续跟踪**继续使用现有的人像语义分割和相邻帧轮廓匹配。
- **MagicTouch 点提示逐帧重分割**在用户点选的帧启动 MediaPipe Interactive Segmenter；随后用上一帧所选轮廓内的稀疏光流移动提示点，并逐帧重新请求分割。结果同时受人像语义分割约束。

如果光流或下一帧分割无法确认所选区域，当前帧会保持透明并计为丢失帧。系统会从最近一次确认的人物帧尝试恢复局部光流，窗口最长 0.2 秒；恢复后更新跟踪锚点，窗口过期后继续保持透明，避免长时间遮挡后误选另一个主体。透明 ProRes 4444、原音频保留、应用到时间线、保存及撤销流程沿用人物抠像现有实现。

## 本地模型

MagicTouch 是通用点提示对象分割模型，不是身份识别模型。MediaPipe 在当前安装版本中通过 `InteractiveSegmenterLegacy` 暴露该 TFLite 接口。模型下载地址和摘要已固定：

- URL：`https://storage.googleapis.com/mediapipe-models/interactive_segmenter/magic_touch/float32/1/magic_touch.tflite`
- SHA-256：`e24338a717c1b7ad8d159666677ef400babb7f33b8ad60c4d96db4ecf694cd25`
- 上游模型卡将模型列为 Apache-2.0，并说明其输入为图像和点提示。详见 [MediaPipe MagicTouch 模型卡](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20MagicTouch.pdf) 与 [Interactive Segmenter API](https://ai.google.dev/edge/api/mediapipe/python/mp/tasks/vision/InteractiveSegmenter)。

默认模型缓存位于 `%USERPROFILE%\.cutvoke\models\`。首次配置使用：

```powershell
uv run --extra person cutvoke-person-model
uv run --extra person cutvoke-person-model --interactive
uv run --extra person cutvoke-person-model --interactive --check
```

第二条命令仅准备 MagicTouch，可选使用 `--output` 覆盖位置；也可用 `CUTVOKE_INTERACTIVE_PERSON_MODEL_PATH` 指定模型文件。界面会在模型缺失时禁用该选项并显示准备命令。

## 验收记录

在 2026-09-25，使用本地演示视频 `output/acceptance/jy-r20-person-20260925/mediapipe-spike/selfie_segmentation_web.mp4` 进行真实逐帧推理。视频为 640×360、25 fps、9 秒。中心点选后处理了 225 帧，MagicTouch 成功帧 225、丢失帧 0；导出为 `yuva444p12le` 透明视频。模型和导出摘要、SHA-256 见 `output/acceptance/jy-r20-person-20260925/mediapipe-spike/magic_touch-acceptance.json`；导出的第 0、4.5、9 秒画面复核见 `output/acceptance/jy-r20-person-20260925/mediapipe-spike/magic_touch_visual_review/magic_touch_contact.png`。模型 `--check` 通过。

回归结果：`tests/test_person_cutout.py`、`tests/test_prepare_person_model.py` 共 16 项通过；人物抠像 Playwright 流程 1/1 通过；`npm run build` 的 TypeScript 检查与 Vite 生产构建通过。

2026-09-26 补充短暂丢帧恢复：用合成纹理人物验证遮挡帧的光流失败后，遮挡解除时可在短窗口内从上一成功帧追踪回移动人物；超窗后不再尝试。`tests/test_person_cutout.py` 当前 12 项通过，人物抠像 Playwright 流程 1/1 通过。该合成序列只验证恢复机制和到期保护，不代表真实人物交叉或长遮挡身份识别。

## 边界

演示片段只含一个人物，证明了点提示、逐帧分割、透明输出和前端提交链路可运行；合成序列验证了 0.2 秒内短暂丢帧的恢复窗口。它仍不能证明两名不同人物接触或互相遮挡时身份不串换，也不能证明完全遮挡后的身份恢复。MagicTouch 的局部点提示和当前稀疏光流仍可能在快速运动、特征不足、接触或完全遮挡时丢失目标或改变分割边界。界面已明确说明这些限制；复杂多人素材仍需独立样本做视觉验收。
