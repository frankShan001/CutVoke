# 可编辑旅行花字与手记标题

内置文字库提供三种可编辑样式。应用后可以在属性面板修改文字、描边、投影和动画。

## 可直接使用的三个预设

| 名称 | presetId | 位置 | 用途 |
| --- | --- | --- | --- |
| 晴日旅拍 | `cutvoke.preset.text.travel_sunshine` | 文字 → 花字 | 黄色字面、白色内描边、青色外描边、深色错位投影；轻弹入场和整词微浮动 |
| 俏皮反应 | `cutvoke.preset.text.travel_reaction` | 文字 → 花字 | 粉色反应词、双描边、错位投影；较短周期的微浮动和倾斜排版 |
| 旅行手记 | `cutvoke.preset.text.travel_note` | 文字 → 信息条 | 纸色底条、深色英文文字、淡入淡出；不叠加循环动作 |

每个预设应用为一个文字片段、一个 `cutvoke.text` 效果。修改一次 `content`，
字面、描边和投影同步更新。网页标题属性可调整两层描边、投影颜色及水平/垂直距离。
循环方式新增“整词微浮动”，周期和动效强度仍使用既有控制。

Agent 示例（通过正常 revision/preview/apply 流程执行）：

```json
{
  "type": "clip.insert",
  "payload": {
    "trackId": "travel-titles",
    "createTrackKind": "text",
    "clipId": "day-out-title",
    "textPresetId": "cutvoke.preset.text.travel_sunshine",
    "text": {"content": "DAY OUT!"},
    "timelineStart": {"num": "0", "den": "1"},
    "timelineEnd": {"num": "3", "den": "1"}
  }
}
```

## 可调整的参数

- `outerStrokeWidth`：外层额外描边宽度，0–16，1080 高度基准像素。
- `outerStrokeColor`：外层描边颜色，`#RRGGBB`。
- `blockShadowX` / `blockShadowY`：二维错位投影距离，-24–24，1080 高度基准像素；随文字缩放和旋转。
- `blockShadowColor`：错位投影颜色，`#RRGGBB`。
- `animLoopStyle: "float"`：整词轻微位移和旋转，保留整词字距与字体排版。

新参数均为可选，没有给旧工程和旧预设强行补默认值。样式和动态共用原生
libass 编译路径，支持静帧定位、连续预览、分窗口和完整导出。这里的投影是二维
错位叠绘，不是 3D 挤出、空间追踪或人物背景分割。
