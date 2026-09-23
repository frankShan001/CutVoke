/** 播放器/时间线辅助工具。全局快捷键由 AppShell 按编辑器视图、焦点与锁定状态管理。 */

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
  const rate = n / de;
  return Number.isFinite(rate) && rate > 0 ? 1 / rate : 1 / 30;
}
