import type { ProjectPreflightIssue } from "./api";

export const LOCATE_PREFLIGHT_ISSUE = "cutvoke:locate-preflight-issue";

export type LocatePreflightIssueEvent = CustomEvent<ProjectPreflightIssue>;
