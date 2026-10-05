/** HTTP API 封装（与 src/cutvoke/core/httpapi.py 对齐）。
    与后端同源 fetch（相对路径 /api/v1/...），不写死 localhost 端口。 */

import type {
  ApiErrorBody,
  CommandResult,
  ExportResult,
  Project,
  ProjectEvent,
  ProjectSummary,
  Rational,
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

export interface ExportRange {
  start: number;
  end: number;
}

export interface ProjectPreflightIssue {
  code: string;
  message: string;
  path?: string;
  clipIds?: string[];
  trackIds?: string[];
  assetIds?: string[];
  resourceKind?: string;
}

export interface ProjectPreflightReport {
  projectId: string;
  revision: string;
  sequenceId: string;
  readyToRender: boolean;
  checkedFileCount: number;
  clipCount: number;
  errors: ProjectPreflightIssue[];
  warnings: ProjectPreflightIssue[];
  files: Array<{
    path: string;
    available: boolean;
    required: boolean;
    resourceKind: string;
    references: Array<{ clipId?: string; trackId?: string; assetId?: string; required: boolean }>;
  }>;
}

/** Read-only file/structure check; does not create an edit or an undo entry. */
export async function preflightProject(
  projectId: string,
  expectedRevision: string,
  range?: ExportRange,
): Promise<ProjectPreflightReport> {
  const query = (revision: string) => sendCommand(
    projectId, "project.preflight", range ? { range } : {}, revision, newCommandId(),
  );
  let result: CommandResult;
  try {
    result = await query(expectedRevision);
  } catch (error) {
    // Another editor may have saved while this dialog was open. Recheck the
    // current revision once, without pretending the query was a local edit.
    if (!(error instanceof ApiFailure) || error.status !== 409) throw error;
    result = await query((await getProject(projectId)).revision);
  }
  const entities = result.changedEntities as unknown as Array<{
    type: string; report?: ProjectPreflightReport;
  }>;
  const report = entities.find((entity) => entity.type === "project_preflight")?.report;
  if (!report || typeof report.readyToRender !== "boolean" ||
      !Array.isArray(report.errors) || !Array.isArray(report.warnings)) {
    throw new ApiFailure({
      status: 0, code: "PREFLIGHT_RESPONSE", message: "素材检查结果不完整，请重试检查", retryable: true,
    });
  }
  return report;
}

export interface ExportQueueReceipt {
  jobId: string;
  status: "queued" | "running";
  projectId: string;
  outPath: string;
  versioned: boolean;
}

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
    仅含 queued/running/终态状态与结果或错误。 */
export interface ExportJob {
  jobId: string;
  projectId: string;
  revision: string;
  outPath: string;
  quality: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted";
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
  loudnessI: number | null;
  /** 每分钟节拍数；后端未检出时为 null。 */
  bpm: number | null;
  /** 源素材时间内检测到的起拍点，使用前需人工复核。 */
  beats: number[];
}

/** POST /audio/analyze（body: {audioPath}）。分析音频响度/BPM 与起拍点。 */
export async function analyzeAudio(input: { path: string }): Promise<AudioAnalysis> {
  return post<AudioAnalysis>("/audio/analyze", { audioPath: input.path });
}

export interface AsrStatus {
  installed: boolean;
  opencvInstalled?: boolean;
  ready: boolean;
  defaultModel: string;
  models: string[];
  devicePreference?: string;
  devices?: string[];
  languages: string[];
  message: string;
}

export interface AsrSegment {
  index: number;
  text: string;
  start: Rational;
  end: Rational;
  words: { text: string; start: Rational; end: Rational }[];
}

export interface AsrResult {
  clipId: string;
  sourceSignature: string;
  model: string;
  device?: "cpu" | "cuda";
  computeType?: string;
  language: string;
  duration: number;
  segmentCount: number;
  segments: AsrSegment[];
}

export interface AsrJob {
  jobId: string;
  projectId: string;
  clipId: string;
  model: string;
  requestedLanguage: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  phase: string;
  progress: number;
  error: string;
  result: AsrResult | null;
}

export async function getAsrStatus(): Promise<AsrStatus> {
  return get<AsrStatus>("/asr/status");
}

export async function startAsrJob(projectId: string, input: {
  clipId: string; model: string; language: string;
}): Promise<AsrJob> {
  return post<AsrJob>(`/projects/${encodeURIComponent(projectId)}/asr-jobs`, input);
}

export async function getAsrJob(jobId: string): Promise<AsrJob> {
  return get<AsrJob>(`/asr-jobs/${encodeURIComponent(jobId)}`);
}

export async function cancelAsrJob(jobId: string): Promise<AsrJob> {
  return post<AsrJob>(`/asr-jobs/${encodeURIComponent(jobId)}/cancel`, {});
}

export interface PersonCutoutStatus {
  installed: boolean;
  modelReady: boolean;
  interactiveModelReady: boolean;
  sam2RuntimeReady?: boolean;
  sam2ModelReady?: boolean;
  sam2Ready?: boolean;
  mediapipeReady?: boolean;
  ready: boolean;
  message: string;
}

export interface PersonCutoutResult {
  outputPath: string;
  frameCount: number;
  fps: string;
  durationSeconds: number;
  audioPreserved: boolean;
  videoCodec: string;
  pixelFormat: string;
  alpha: boolean;
  selectedFrames?: number | null;
  lostFrames?: number | null;
  maxSeparatedComponents?: number | null;
}

export interface PersonCutoutJob {
  jobId: string;
  projectId: string;
  clipId: string;
  sourceSignature: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled" | "applying" | "applied";
  phase: string;
  progress: number;
  error: string;
  selectionMode?: "all_people" | "selected_person";
  trackingMode?: "semantic" | "magic_touch" | "sam2";
  selectionPoint?: [number, number] | null;
  selectionPoints?: [number, number, 0 | 1][] | null;
  selectionAtSeconds?: number | null;
  selectionPrompts?: {
    atSeconds: number;
    points: [number, number, 0 | 1][];
  }[] | null;
  result: PersonCutoutResult | null;
}

export interface PersonCutoutApplyResult {
  job: PersonCutoutJob;
  command: CommandResult;
}

export async function getPersonCutoutStatus(): Promise<PersonCutoutStatus> {
  return get<PersonCutoutStatus>("/person-cutout/status");
}

export function personCutoutPreviewUrl(
  projectId: string, clipId: string, atSeconds?: number,
): string {
  const query = new URLSearchParams({ clipId });
  if (typeof atSeconds === "number" && Number.isFinite(atSeconds)) {
    query.set("atSeconds", String(atSeconds));
  }
  return `/api/v1/projects/${encodeURIComponent(projectId)}/person-cutout-preview?${query.toString()}`;
}

export async function startPersonCutoutJob(projectId: string, input: {
  clipId: string; edgeSoftness: number;
  selectionMode?: "all" | "selected";
  trackingMode?: "semantic" | "magic_touch" | "sam2";
  selectionPoint?: { x: number; y: number } | null;
  selectionPoints?: { x: number; y: number; label: 0 | 1 }[];
  selectionAtSeconds?: number;
  selectionPrompts?: {
    atSeconds: number;
    points: { x: number; y: number; label: 0 | 1 }[];
  }[];
}): Promise<PersonCutoutJob> {
  return post<PersonCutoutJob>(
    `/projects/${encodeURIComponent(projectId)}/person-cutout-jobs`, input);
}

export async function getPersonCutoutJob(jobId: string): Promise<PersonCutoutJob> {
  return get<PersonCutoutJob>(`/person-cutout-jobs/${encodeURIComponent(jobId)}`);
}

export async function cancelPersonCutoutJob(jobId: string): Promise<PersonCutoutJob> {
  return post<PersonCutoutJob>(`/person-cutout-jobs/${encodeURIComponent(jobId)}/cancel`, {});
}

export async function applyPersonCutoutJob(
  projectId: string, jobId: string,
): Promise<PersonCutoutApplyResult> {
  return post<PersonCutoutApplyResult>(
    `/projects/${encodeURIComponent(projectId)}/person-cutout-jobs/${encodeURIComponent(jobId)}/apply`,
    {},
  );
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

export interface ResourcePackEntry {
  packId: string;
  version: string;
  manifestSha256: string;
  resourceCount: number;
  presetCount: number;
  stickerCount: number;
  backgroundCount: number;
  provenanceCoverage?: {
    resourceCount: number;
    licenseDeclaredCount: number;
    sourceDeclaredCount: number;
    completeCount: number;
    incompleteCount: number;
  };
  fileCount: number;
  offlineAvailable: boolean;
  missingFiles: string[];
  invalidFiles: string[];
  builtin: boolean;
  active: boolean;
  signatureStatus: "bundled" | "verified" | "unknown_publisher" | "unsigned";
  publisherVerified: boolean;
  publisherId: string;
  keyId: string;
  fingerprint: string;
}

export interface ResourcePackManagerStatus {
  active: ResourcePackEntry;
  installed: ResourcePackEntry[];
  history: { packId: string; version: string }[];
  canRollback: boolean;
}

export interface ResourceRegistryPackage {
  packId: string;
  version: string;
  name: string;
  url: string;
  sha256: string;
  sizeBytes: number;
}

export interface ResourceRegistry {
  url: string;
  registryId?: string;
  displayName?: string;
  publisherId?: string;
  keyId?: string;
  fingerprint?: string;
  signatureStatus?: "verified" | "unknown_publisher";
  publisherVerified?: boolean;
  packages?: ResourceRegistryPackage[];
  lastCheckedAt?: string;
  lastError?: string;
}

export interface ResourceRegistryDownloadResult {
  installed: ResourcePackEntry;
  bytesReceived: number;
  registryId: string;
  catalogPublisherId: string;
  catalogSignatureStatus: "verified" | "unknown_publisher";
}

let resourcePackStatusRequest: Promise<ResourcePackManagerStatus> | null = null;

export function getResourcePackManagerStatus(): Promise<ResourcePackManagerStatus> {
  // Creative domains share one audited pack. Coalesce concurrent refreshes;
  // release the request on settlement so later mutations still refresh it.
  return resourcePackStatusRequest ??= get<ResourcePackManagerStatus>("/resource-packs")
    .finally(() => { resourcePackStatusRequest = null; });
}

export async function listResourceRegistries(): Promise<{ registries: ResourceRegistry[] }> {
  return get<{ registries: ResourceRegistry[] }>("/resource-packs/registries");
}

export async function addResourceRegistry(url: string): Promise<ResourceRegistry> {
  return post<ResourceRegistry>("/resource-packs/registries/add", { url });
}

export async function refreshResourceRegistries(url?: string): Promise<{ registries: ResourceRegistry[] }> {
  return post<{ registries: ResourceRegistry[] }>("/resource-packs/registries/refresh", url ? { url } : {});
}

export async function removeResourceRegistry(url: string): Promise<{ registries: ResourceRegistry[] }> {
  return post<{ registries: ResourceRegistry[] }>("/resource-packs/registries/remove", { url });
}

export async function downloadResourceRegistryPackage(
  url: string, packId: string, version: string,
): Promise<ResourceRegistryDownloadResult> {
  return post<ResourceRegistryDownloadResult>("/resource-packs/registries/download", {
    url, packId, version,
  });
}

/** POST /projects/{id}/exports：提交异步导出，可选按时间线区间截取成片。 */
export async function enqueueExportProject(
  projectId: string,
  outPath: string,
  quality: ExportQuality = "high",
  range?: ExportRange,
  overwrite = true,
): Promise<ExportQueueReceipt> {
  const body: Record<string, unknown> = { outPath, quality, overwrite };
  if (range) body.range = range;
  return post<ExportQueueReceipt>(`/projects/${encodeURIComponent(projectId)}/exports`, body);
}

export async function installResourcePack(file: File): Promise<ResourcePackEntry> {
  const path = "/resource-packs/install";
  const response = await fetch(API_BASE + path, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/zip" },
    body: file,
  });
  const data = await readJson(response);
  if (!response.ok) throw normalizeError(data, response.status, path);
  return data as ResourcePackEntry;
}

export async function trustResourcePackPublisher(
  publisherId: string,
  publicKeyPem: string,
  fingerprint: string,
): Promise<ResourcePackManagerStatus> {
  return post<ResourcePackManagerStatus>("/resource-packs/trust", {
    publisherId, publicKeyPem, fingerprint,
  });
}

export async function activateResourcePack(
  packId: string,
  version: string,
): Promise<ResourcePackManagerStatus> {
  return post<ResourcePackManagerStatus>("/resource-packs/activate", { packId, version });
}

export async function rollbackResourcePack(): Promise<ResourcePackManagerStatus> {
  return post<ResourcePackManagerStatus>("/resource-packs/rollback", {});
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
    { ...body, actor: body.actor ?? { kind: "human", id: "ui" } },
  );
}

export type { Template };
