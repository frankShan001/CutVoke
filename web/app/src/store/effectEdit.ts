/** 效果栈 / 资源 / 预设 命令封装（J01）。
 *
 * 与 clipEdit.ts 同风格：统一走 runCommand（自动 REVISION_CONFLICT 重试 + 结构化错误）。
 * 契约：src/cutvoke/core/service.py 的 _h_effect_bypass / _h_effect_reorder /
 * _h_resource_* / _h_preset_*。effect.add / effect.remove 复用 clipEdit 的既有实现，不重复造。
 */

import type { Dispatch } from "react";
import type { EditorAction, EditorState } from "./editor";
import { runCommand, type ActionResult } from "./actions";
import { addEffect, removeEffect } from "./clipEdit";

/** 应用一个效果到片段（effect.add）。params 缺省用后端默认值（空对象由后端补齐）。 */
export async function addEffectToClip(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { clipId: string; effectId: string; params?: Record<string, unknown> },
): Promise<ActionResult> {
  return addEffect(dispatch, state, {
    clipId: input.clipId,
    effectId: input.effectId,
    params: input.params ?? {},
  });
}

/** 移除片段上某个 effectId 的全部实例（effect.remove）。 */
export async function removeEffectFromClip(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { clipId: string; effectId: string },
): Promise<ActionResult> {
  return removeEffect(dispatch, state, input);
}

/** 旁路 / 恢复片段上的效果（effect.bypass {clipId, effectId, enabled}）。 */
export async function bypassEffect(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { clipId: string; effectId: string; enabled: boolean },
): Promise<ActionResult> {
  const res = await runCommand(dispatch, state, "effect.bypass", {
    clipId: input.clipId,
    effectId: input.effectId,
    enabled: input.enabled,
  });
  if (res.ok) {
    dispatch({
      type: "STATUS_SET",
      severity: "ok",
      text: input.enabled ? "已恢复该效果" : "已旁路该效果（保留在栈中）",
    });
  }
  return res;
}

/** 重排效果栈（effect.reorder {clipId, effectId, toIndex}）；顺序即渲染合成顺序。 */
export async function reorderEffect(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { clipId: string; effectId: string; toIndex: number },
): Promise<ActionResult> {
  const res = await runCommand(dispatch, state, "effect.reorder", {
    clipId: input.clipId,
    effectId: input.effectId,
    toIndex: input.toIndex,
  });
  if (res.ok) dispatch({ type: "STATUS_SET", severity: "ok", text: "已调整效果顺序" });
  return res;
}

/** 局部更新片段上某效果实例的参数（effect.update {clipId, effectId, params}，深合并）。
 *  H01 画布文字编辑用它写回 cutvoke.text 的几何参数 x/y/scale/rotation。
 *  契约字段名固定，不可改名（见 team-lead 派发契约）。 */
export async function updateEffect(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { clipId: string; effectId: string; params: Record<string, unknown> },
): Promise<ActionResult> {
  const res = await runCommand(dispatch, state, "effect.update", {
    clipId: input.clipId,
    effectId: input.effectId,
    params: input.params,
  });
  if (res.ok) dispatch({ type: "STATUS_SET", severity: "ok", text: "已更新文字位置" });
  return res;
}

/** 收藏 / 取消收藏一个效果（resource.favorite / resource.unfavorite，工程级）。 */
export async function setFavorite(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { effectId: string; favorite: boolean },
): Promise<ActionResult> {
  const res = await runCommand(
    dispatch,
    state,
    input.favorite ? "resource.favorite" : "resource.unfavorite",
    { effectId: input.effectId },
  );
  if (res.ok) {
    dispatch({
      type: "STATUS_SET",
      severity: "ok",
      text: input.favorite ? "已加入收藏" : "已取消收藏",
    });
  }
  return res;
}

/** 保存个人预设（preset.save）：{name, clipId} 采用片段当前效果栈，或 {name, effects}。 */
export async function savePreset(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { name: string; clipId?: string; effects?: { effectId: string; params?: Record<string, unknown> }[] },
): Promise<ActionResult> {
  const payload: Record<string, unknown> = { name: input.name };
  if (input.effects && input.effects.length) payload.effects = input.effects;
  else if (input.clipId) payload.clipId = input.clipId;
  const res = await runCommand(dispatch, state, "preset.save", payload);
  if (res.ok) dispatch({ type: "STATUS_SET", severity: "ok", text: `已保存预设「${input.name}」` });
  return res;
}

/** 应用个人预设（preset.apply {clipId, presetId, mode}）。mode 缺省 merge。 */
export async function applyPreset(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { clipId: string; presetId: string; mode?: "merge" | "replace" },
): Promise<ActionResult> {
  const res = await runCommand(dispatch, state, "preset.apply", {
    clipId: input.clipId,
    presetId: input.presetId,
    mode: input.mode ?? "merge",
  });
  if (res.ok) {
    dispatch({
      type: "STATUS_SET",
      severity: "ok",
      text: input.mode === "replace" ? "已应用预设（替换同类）" : "已应用预设（合并）",
    });
  }
  return res;
}

/** 删除个人预设（preset.delete {presetId}）。 */
export async function deletePreset(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: { presetId: string },
): Promise<ActionResult> {
  const res = await runCommand(dispatch, state, "preset.delete", { presetId: input.presetId });
  if (res.ok) dispatch({ type: "STATUS_SET", severity: "ok", text: "已删除预设" });
  return res;
}
