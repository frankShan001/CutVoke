import type { ProjectPreflightIssue } from "./api";
import { tr } from "../i18n";

export const LOCATE_PREFLIGHT_ISSUE = "cutvoke:locate-preflight-issue";

export type LocatePreflightIssueEvent = CustomEvent<ProjectPreflightIssue>;

/** Service-owned issue codes are stable; raw diagnostics are retained separately. */
export function preflightSummary(issue: ProjectPreflightIssue): string {
  switch (issue.code) {
    case "MISSING_SOURCE_PATH": return tr("片段没有素材路径，请重新链接素材。");
    case "MISSING_FILE":
    case "NOT_A_FILE":
    case "UNREADABLE_FILE":
    case "EMPTY_FILE":
      return issue.resourceKind === "lut" ? tr("自定义 LUT 文件不可用，请重新导入 LUT。") : tr("素材文件不可用，请在素材库中重新链接。");
    case "EMPTY_TIMELINE": return tr("没有可输出的视频或音频片段，请先将素材放入时间线。");
    case "INVALID_EFFECT_PARAMETER": return tr("转场时长无效，请重新设置转场。");
    case "INVALID_PROJECT": return tr("工程结构无效，请检查轨道和片段设置。");
    default: return tr("工程检查有提醒，请查看原始诊断。");
  }
}
