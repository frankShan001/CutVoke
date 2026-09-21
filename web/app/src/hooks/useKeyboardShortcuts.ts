/** 播放器/时间线辅助工具（无全局快捷键——Web 浏览器会与浏览器快捷键冲突）。
    仅导出 UI 按钮可调用的纯函数；不挂任何全局 keydown 监听。 */

import { frameStep } from "../components/timeline/snap";

export { frameStep };

/** 触发播放/暂停（供外部/按钮调用；Player 在 window 上监听该事件）。 */
export function notifyTogglePlay() {
  window.dispatchEvent(new CustomEvent("cutvoke:toggle-play"));
}

/** 从工程 fps 得到单帧时长（秒）。 */
export function fpsToStep(fps: { num: string; den: string } | undefined): number {
  if (!fps) return 1 / 30;
  const n = Number(fps.num);
  const de = Number(fps.den);
  return de ? n / de : 30;
}