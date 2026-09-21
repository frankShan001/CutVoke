/** HTTP API 封装（与 src/cutvoke/core/httpapi.py 对齐）。
    与后端同源 fetch（相对路径 /api/v1/...），不写死 localhost 端口。 */

import type {
  ApiErrorBody,
  CommandResult,
  ExportResult,
  Project,
  ProjectEvent,
  ProjectSummary,
  Template,
  TemplateApplyBody,
  TemplateApplyResult,
  TemplatesResponse,
} from "../types/api";

export type { CommandResult, ExportResult, Project, ProjectEvent, ProjectSummary };

export const API_BASE = "/api/v1";

export class ApiFailure extends Error {
  status: number;
  code: string;
  details?: unknown;
  retryable?: boolean;
  committed?: boolean;
  correlationId?: string;
  path?: string;

  constructor(opts: {
    status: number;
    code: string;
    message: string;
    details?: unknown;
    retryable?: boolean;
    committed?: boolean;
    correlationId?: string;
    path?: string;
  }) {
    super(opts.message);
    this.name = "ApiFailure";
    this.status = opts.status;
    this.code = opts.code;
    this.details = opts.details;
    this.retryable = opts.retryable;
    this.committed = opts.committed;
    this.correlationId = opts.correlationId;
    this.path = opts.path;
  }
}

async function readJson(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    return undefined;
  }
}

function normalizeError(data: unknown, status: number, path: string): ApiFailure {
  const err = (data as { error?: ApiErrorBody } | undefined)?.error;
  return new ApiFailure({
    status,
    code: err?.code || `HTTP_${status}`,
    message: err?.message || `请求失败（HTTP ${status}）`,
    details: err?.details,
    retryable: err?.retryable,
    committed: err?.committed,
    correlationId: err?.correlationId,
    path,
  });
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(API_BASE + path, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  const data = await readJson(res);
  if (!res.ok) throw normalizeError(data, res.status, path);
  return data as T;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(API_BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await readJson(res);
  if (!res.ok) throw normalizeError(data, res.status, path);
  return data as T;
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(API_BASE + path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await readJson(res);
  if (!res.ok) throw normalizeError(data, res.status, path);
  return data as T;
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

export interface ProjectListItem {
  /** 稳定标识（后端 projectId，重命名不变）。 */
  id: string;
  /** 人类可读名称；后端未提供时为空串（不回填 id，避免内部标识外泄到 UI）。 */
  name: string;
  revision: string;
  /** ISO 时间串；后端未提供时为空串。 */
  updatedAt: string;
}

/** 后端列表项的两种形态（旧：裸 id 字符串；新：对象）。 */
type RawProjectItem =
  | string
  | { id?: string; projectId?: string; name?: string; revision?: string; updatedAt?: string };

/** 归一化：兼容后端未升级（裸 id 串）与已升级（含 name/revision/updatedAt）两种返回。 */
function normalizeProjectItem(p: RawProjectItem): ProjectListItem {
  if (typeof p === "string") {
    // 旧格式：后端仅回裸 id，无名称信息 → name 留空，由 UI 决定人类可读回退。
    return { id: p, name: "", revision: "", updatedAt: "" };
  }
  const id = p.id || p.projectId || "";
  return {
    id,
    // 刻意不把 id 回填进 name：name 为空即“未命名”，避免内部标识外泄到首页。
    name: p.name || "",
    revision: p.revision || "",
    updatedAt: p.updatedAt || "",
  };
}

/** GET /projects → {projects:[...]}（兼容裸 id 串与对象两种格式）。 */
export async function listProjects(): Promise<ProjectListItem[]> {
  const data = await get<{ projects: RawProjectItem[] }>("/projects");
  return (data.projects || []).map(normalizeProjectItem);
}

/** PATCH /projects/{id} → 重命名工程（body {name}）。 */
export async function renameProject(
  projectId: string,
  name: string,
): Promise<{ projectId?: string; name?: string }> {
  return patch<{ projectId?: string; name?: string }>(
    `/projects/${encodeURIComponent(projectId)}`,
    { name },
  );
}

export interface CreateProjectResult {
  schemaVersion: string;
  projectId: string;
  revision: string;
  sequence?: unknown;
}

/** POST /projects */
export async function createProject(input: {
  width: number;
  height: number;
  fps: number;
  projectId?: string;
  /** 人类可读工程名（可选）。后端 name_hint 会持久化为工程名。 */
  name?: string;
}): Promise<CreateProjectResult> {
  return post<CreateProjectResult>("/projects", input);
}

/** GET /projects/{id} → 扁平 Project（含 revision）。 */
export async function getProject(projectId: string): Promise<Project> {
  return get<Project>(`/projects/${encodeURIComponent(projectId)}`);
}

export interface EditLease {
  projectId: string;
  leaseId: string;
  owner: string;
  expiresAt: number;
  acquiredAt: number;
}

export interface EditLockStatus {
  locked: boolean;
  lease: EditLease | null;
}

/** GET /projects/{id}/edit-lock：只读查询 Agent 编辑租约。 */
export async function getEditLock(projectId: string): Promise<EditLockStatus> {
  return get<EditLockStatus>(`/projects/${encodeURIComponent(projectId)}/edit-lock`);
}

/** GET /projects/{id}/summary */
export async function getProjectSummary(projectId: string): Promise<ProjectSummary> {
  return get<ProjectSummary>(`/projects/${encodeURIComponent(projectId)}/summary`);
}

export interface CommandEnvelope {
  type: string;
  payload: Record<string, unknown>;
  expectedRevision: string;
  commandId: string;
  actor?: { kind: string; id: string };
  editLeaseId?: string;
}

/** POST /projects/{id}/commands → CommandResult（无 ok 字段）。 */
export async function sendCommand(
  projectId: string,
  type: string,
  payload: Record<string, unknown>,
  expectedRevision: string,
  commandId: string,
  editLeaseId?: string,
): Promise<CommandResult> {
  const body: CommandEnvelope = {
    type,
    payload,
    expectedRevision,
    commandId,
    actor: { kind: "human", id: "ui" },
  };
  if (editLeaseId) body.editLeaseId = editLeaseId;
  return post<CommandResult>(`/projects/${encodeURIComponent(projectId)}/commands`, body);
}

export type ExportQuality = "high" | "low";

/** POST /projects/{id}/export（字段：outPath + quality，quality 缺省后端按 high）。 */
export async function exportProject(
  projectId: string,
  outPath: string,
  quality: ExportQuality = "high",
): Promise<ExportResult> {
  return post<ExportResult>(`/projects/${encodeURIComponent(projectId)}/export`, {
    outPath,
    quality,
  });
}

/** 导出任务（GET /projects/{id}/exports 返回的单个任务）。后端无百分比进度字段，
    仅含状态 running/succeeded/failed 与结果/错误。 */
export interface ExportJob {
  jobId: string;
  projectId: string;
  revision: string;
  outPath: string;
  quality: string;
  status: "running" | "succeeded" | "failed";
  result: ExportResult | null;
  error: { code: string; message: string } | null;
  createdAt?: string;
  updatedAt?: string;
}

/** GET /projects/{id}/exports → 该工程的导出任务列表（最新在前）。 */
export async function listExportJobs(projectId: string): Promise<ExportJob[]> {
  const data = await get<{ jobs: ExportJob[] }>(
    `/projects/${encodeURIComponent(projectId)}/exports`,
  );
  return data.jobs || [];
}

/** 封面导出结果（POST /projects/{id}/cover）。 */
export interface CoverResult {
  outPath: string;
  width: number;
  height: number;
}

/** POST /projects/{id}/cover（字段：outPath + t 秒 + 可选 width）。导出指定时间点单帧封面图。 */
export async function exportCover(
  projectId: string,
  outPath: string,
  t: number,
  width?: number,
): Promise<CoverResult> {
  const body: Record<string, unknown> = { outPath, t };
  if (width !== undefined) body.width = width;
  return post<CoverResult>(`/projects/${encodeURIComponent(projectId)}/cover`, body);
}

/** 音频分析结果（POST /audio/analyze）。bpm 可能为空（未检出）。 */
export interface AudioAnalysis {
  duration: number;
  /** 集成响度 LUFS。 */
  loudnessI: number;
  /** 每分钟节拍数；后端未检出时为 null。 */
  bpm: number | null;
}

/** POST /audio/analyze（body: {path}）。分析音频响度/BPM，辅助卡点。 */
export async function analyzeAudio(input: { path: string }): Promise<AudioAnalysis> {
  return post<AudioAnalysis>("/audio/analyze", { path: input.path });
}

/** 效果能力清单（GET /effects → {effects:[...]}，兼容 capabilities）。 */
export interface EffectParamSchema {
  type?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  description?: unknown;
  properties?: Record<string, EffectParamSchema>;
  additionalProperties?: boolean;
  [k: string]: unknown;
}

export interface EffectSpec {
  effectId: string;
  version: string;
  name: string;
  category: string;
  description?: string;
  parameters?: { properties?: Record<string, EffectParamSchema> };
  defaults?: Record<string, unknown>;
  animatable?: string[];
}

export async function listEffects(): Promise<EffectSpec[]> {
  const data = await get<{ effects: EffectSpec[] }>("/effects");
  return data.effects || [];
}

/** GET /projects/{id}/events?since= */
export async function listEvents(
  projectId: string,
  since: string,
): Promise<ProjectEvent[]> {
  const data = await get<{ events: ProjectEvent[] }>(
    `/projects/${encodeURIComponent(projectId)}/events?since=${encodeURIComponent(since)}`,
  );
  return data.events || [];
}

/** 生成客户端幂等 commandId。 */
export function newCommandId(): string {
  const ts = Date.now().toString(36);
  const rand = Math.random().toString(36).slice(2, 8);
  return `cmd_${ts}_${rand}`;
}

// ---------------------------------------------------------------------------
// 资源包（J10）：打包 / 解包打开 / 检视——异机交接与恢复
// ---------------------------------------------------------------------------

/** POST /projects/{id}/package 结果。 */
export interface PackageResult {
  outPath: string;
  format: string;
  size: number;
  clipCount: number;
  assetCount: number;
}

/** POST /projects/{id}/package（body: {outPath?}）。把工程打包为自包含 .cutvokepack.zip。 */
export async function packageProject(
  projectId: string,
  outPath?: string,
): Promise<PackageResult> {
  return post<PackageResult>(`/projects/${encodeURIComponent(projectId)}/package`, {
    outPath: outPath || undefined,
  });
}

/** POST /packages/open 结果。 */
export interface OpenPackageResult {
  projectId: string;
  name: string;
  importedAssets: number;
  /** 缺失素材/未知效果等非阻断警示。 */
  warnings: string[];
}

/** POST /packages/open（body: {packagePath, targetDir?}）。解包并导入为新工程。 */
export async function openPackage(
  packagePath: string,
  targetDir?: string,
): Promise<OpenPackageResult> {
  return post<OpenPackageResult>("/packages/open", {
    packagePath,
    targetDir: targetDir || undefined,
  });
}

/** GET /packages/inspect?path= → 只读检视 manifest（不落盘）。 */
export interface PackageInspect {
  format?: string;
  version?: string;
  projectId?: string;
  clipCount?: number;
  assetCount?: number;
  missing?: string[];
  compatibility?: unknown;
}

export async function inspectPackage(packagePath: string): Promise<PackageInspect> {
  return get<PackageInspect>(
    `/packages/inspect?path=${encodeURIComponent(packagePath)}`,
  );
}

// ---------------------------------------------------------------------------
// J12 模板库（GET /templates + POST /projects/{id}/templates/apply）
// ---------------------------------------------------------------------------

/** 列表查询参数。 */
export interface ListTemplatesParams {
  /** 按分类筛选。 */
  category?: string;
  /** 按模板 id 查询单个模板（不存在 → 后端 404 NOT_FOUND）。 */
  templateId?: string;
}

/** GET /api/v1/templates → {templates, count, skipped}。 */
export async function listTemplates(params?: ListTemplatesParams): Promise<TemplatesResponse> {
  const qs = new URLSearchParams();
  if (params?.category) qs.set("category", params.category);
  if (params?.templateId) qs.set("templateId", params.templateId);
  const q = qs.toString();
  return get<TemplatesResponse>(`/templates${q ? `?${q}` : ""}`);
}

/** POST /api/v1/projects/{id}/templates/apply → {revision, changedEntities}。 */
export async function applyTemplate(
  projectId: string,
  body: TemplateApplyBody,
): Promise<TemplateApplyResult> {
  return post<TemplateApplyResult>(
    `/projects/${encodeURIComponent(projectId)}/templates/apply`,
    body,
  );
}

export type { Template };
