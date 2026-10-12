# 「潮汐藏馆」转场前黑闪修复

## 复现与原因

工程：`tide-gallery-20260927`，「潮汐藏馆｜原始可编辑工程」，revision 4，40 秒，13 个片段。使用原 SQLite 数据库的在线备份、真实原素材及已生成的窗口缓存，在 8821 测试服务复现。

四处转场位于 8、16、24、32 秒，恰好对应预览窗口的切换点。播放器以窗口 URL 为 `video` 的 React key，进入下一窗口时立即移除旧元素；新元素还未解码，首帧接口也尚未返回，所以显示画布底色。

修复前首个边界测到 5 次无画面采样，约 124ms。旧段末尾和新段首帧平均 RGB 分别约 144、143，显示黑闪发生在播放器换源间隙。另用 FFmpeg 检查旧窗口最后 0.3 秒的 9 帧及新窗口最初 1 秒的 30 帧，未发现近黑帧。

证据目录：`output/playwright/transition-blackflash-20261003/`；修复前见 `before-boundary.json`，编码画面见 `encoded-boundary.json`。

## 修改

- 新增 `useVideoHandoff.ts`：连续播放进入相邻窗口时，在旧视频移除前将其最后的解码画面保留到 Canvas；下一段加载与解码期间继续显示该画面。
- 下一段播放帧送交浏览器合成后再撤下保留画面。暂停或取消后的已解码视频使用绘制帧回调完成就绪状态，避免等待一个不会再播放的帧。
- 保留帧按工程 ID、画面内容标记和源素材恢复标记隔离；跨工程、画面修改、跳转和停止不会复用连续播放的旧帧。待执行的回调在换源时取消。
- 加载提示改为「已保留当前画面，正在准备下一段」。不重复请求昂贵的工程合成帧；初次打开的优先首帧功能继续保留。

浏览器帧提交机制参考：[MDN requestVideoFrameCallback](https://developer.mozilla.org/en-US/docs/Web/API/HTMLVideoElement/requestVideoFrameCallback)。不支持该 API 的浏览器使用已解码数据与绘制帧回调。

## 验收

| 场景 | 结果 | 证据 |
|---|---|---|
| 原工程连续播放过四处转场 | 逐绘制帧检查保留画面和实际视频；全部边界无空画面采样 | `final-boundaries.json`、`after-all-transitions.png` |
| 故意延迟下一窗口的媒体响应 | 画面持续可见，保留帧 RGB 均值约 143.7，显示正确等待说明 | `slow-handoff-retained-frame.png`、`wait-cancel-result.json` |
| 初次等待和转场等待时取消 | 加载完成仍暂停在窗口开头，等待说明消失；再次点击播放可继续；无页面异常 | `wait-cancel-result.json` |
| 构建与生效 | TypeScript / Vite 构建通过；当前 8787 服务提供新构建，刷新页面即可生效 | `final-verification.json` |

分段仍需解码，切换期间会短暂保留上一帧。本次修复了空画面黑闪；渲染慢于播放速度时仍会显示等待。此次未更改原工程、原素材或转场参数。

诊断过程中有一份重复安装监控脚本的采样已标为 `after-boundaries-duplicate-probe.json`，不用于验收；以最终新浏览器实例的 `final-boundaries.json` 为准。
