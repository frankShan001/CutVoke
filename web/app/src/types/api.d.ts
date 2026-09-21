/* CutVoke 前后端契约类型（与 src/cutvoke/core/model.py + protocol.py 对齐）。
   注意：GET /projects/{id} 返回扁平的 Project.to_dict()，
   片段源路径在 assetRef.sourcePath；时间一律 {num, den} 十进制字符串。 */

/** 有理数时间（num/den，十进制字符串，已约分）。 */
export interface Rational {
  num: string;
  den: string;
}

export interface AssetReference {
  assetId: string;
  sourcePath: string;
  fingerprint?: string;
}

export interface EffectInstance {
  effectId: string;
  version: string;
  params: Record<string, unknown>;
  /** 旁路标记（effect.bypass 写入；false=保留在栈中但不参与渲染，缺省视为生效）。 */
  enabled?: boolean;
}

/** 关键帧插值方式（与后端 Keyframe.VALID_INTERP 对齐）。 */
export type KeyframeInterpolation = "linear" | "ease-in" | "ease-out";

export interface Keyframe {
  id: string;
  time: Rational;
  value: unknown;
  interpolation: KeyframeInterpolation;
}

export interface Clip {
  id: string;
  assetRef: AssetReference;
  timelineStart: Rational;
  timelineEnd: Rational;
  sourceStart: Rational;
  speed?: Rational;
  /** 相对音量（1.0=原声，0=静音，可 >1）。clip.audio 设置。 */
  volume?: number;
  /** 淡入时长（秒）。clip.audio 设置。 */
  fadeIn?: number;
  /** 淡出时长（秒）。clip.audio 设置。 */
  fadeOut?: number;
  effects?: EffectInstance[];
  linked?: boolean;
  hidden?: boolean;
  keyframes?: Record<string, Keyframe[]>;
}

export interface Track {
  id: string;
  kind: string; // "video" | "audio" | "text" | "caption"
  clips: Clip[];
  locked?: boolean;
  muted?: boolean;
  visible?: boolean;
}

export interface Sequence {
  id: string;
  width: number;
  height: number;
  fps: Rational;
  audioSampleRate?: number;
  tracks: Track[];
  captions?: Caption[];
  markers?: Marker[];
}

export interface Caption {
  id: string;
  text: string;
  start: Rational;
  end: Rational;
  /** 样式字段（后端 caption.add/update 支持；缺省时后端给默认值）。 */
  fontSize?: number;
  color?: string;
  strokeColor?: string;
  strokeWidth?: number;
  /** 字幕背景色；空串=无背景。 */
  background?: string;
  align?: "left" | "center" | "right";
  bold?: boolean;
  /** 入场动画（淡入）时长，毫秒；0=无。后端 caption.update 支持。 */
  animIn?: number;
  /** 出场动画（淡出）时长，毫秒；0=无。后端 caption.update 支持。 */
  animOut?: number;
  /** 画布几何（H01）：x 归一化 0~1，默认 0.5 居中。 */
  x?: number;
  /** 画布几何（H01）：y 归一化 0~1，默认 0.5 居中。 */
  y?: number;
  /** 画布几何（H01）：缩放 0.1~5，默认 1。 */
  scale?: number;
  /** 画布几何（H01）：旋转角度 -180~180，默认 0。 */
  rotation?: number;
}

export interface Marker {
  id: string;
  name: string;
  time: Rational;
}

/** GET /projects/{id} 返回体（Project.to_dict()）。 */
export interface Project {
  schemaVersion: string;
  projectId: string;
  revision: string;
  sequence: Sequence;
}

/** 命令成功结果（CommandResult.to_dict()）。注意：不含 ok 字段。 */
export interface CommandResult {
  commandId: string;
  previousRevision: string;
  revision: string;
  transactionId: string;
  changedEntities: ChangedEntity[];
  warnings: string[];
}

export interface ChangedEntity {
  type: string;
  id: string;
  change: string;
}

/** 结构化错误（Error.to_dict() 的 error 字段）。 */
export interface ApiErrorBody {
  code: string;
  message: string;
  details?: unknown;
  retryable?: boolean;
  committed?: boolean;
  correlationId?: string;
}

/** 事件（Event.to_dict()）。 */
export interface ProjectEvent {
  eventId: string;
  projectId: string;
  previousRevision: string;
  revision: string;
  commandId: string;
  transactionId: string;
  type: string;
  changedEntities: ChangedEntity[];
  actor?: { kind: string; id: string };
}

/** 导出结果（render.render() 返回）。 */
export interface ExportResult {
  output_path: string;
  duration: number;
  width: number;
  height: number;
  has_audio?: boolean;
  warnings?: string[];
  /** 导出任务幂等标识（POST /export 回写）。 */
  jobId?: string;
  /** 命中缓存（同一请求此前已成功）。 */
  cached?: boolean;
}

/** 工程摘要（GET /projects/{id}/summary）。 */
export interface ProjectSummary {
  projectId: string;
  revision: string;
  trackCount: number;
  clipCount: number;
  width: number;
  height: number;
  fps: Rational;
}

// ---------------------------------------------------------------------------
// J12 模板库（GET /templates + POST /projects/{id}/templates/apply）
// ---------------------------------------------------------------------------

/** 画幅（后端枚举）。 */
export type TemplateAspect = "16:9" | "9:16" | "1:1";

/** 模板槽位（素材占位）。 */
export interface TemplateSlot {
  key: string;
  kind: string;
  label: string;
  duration: number;
}

/** 模板元信息（GET /templates 列表项）。后端字段即此，不要擅自扩展。 */
export interface Template {
  id: string;
  name: string;
  description: string;
  aspect: TemplateAspect;
  category: string;
  slotCount: number;
  slots: TemplateSlot[];
  hasBgm: boolean;
  captionCount: number;
  /** 转场效果 id；无转场为 null。 */
  transition: string | null;
  estimatedSeconds: number;
}

/** 加载失败的坏模板（正常为 []），UI 须如实提示，不可静默吞掉。 */
export interface TemplateLoadSkip {
  id?: string;
  name?: string;
  error?: string;
}

/** GET /templates 返回体。 */
export interface TemplatesResponse {
  templates: Template[];
  count: number;
  skipped: TemplateLoadSkip[];
}

/** 套用请求体（POST /projects/{id}/templates/apply）。 */
export interface TemplateApplyBody {
  templateId: string;
  /** slotKey → assetId|sourcePath；缺省则后端用内置资产占位。 */
  sources?: Record<string, string>;
  /** 默认 false（追加，不破坏已有时间线）。 */
  clearExisting?: boolean;
  expectedRevision?: string;
}

/** template_applied 汇总事件（changedEntities 内）。 */
export interface TemplateAppliedEvent {
  type: "template_applied";
  templateId: string;
  name: string;
  aspect: string;
  canvas: [number, number];
  origin: string;
  durationSec: number;
  placeholderCount: number;
  clearedExisting: boolean;
}

/** 套用结果中的变更实体：汇总事件 + 普通实体（clip 可能带 placeholder/slotKey）。 */
export type TemplateApplyChangedEntity =
  | TemplateAppliedEvent
  | (ChangedEntity & { placeholder?: boolean; slotKey?: string });

/** POST /projects/{id}/templates/apply 返回体。 */
export interface TemplateApplyResult {
  revision: string;
  changedEntities: TemplateApplyChangedEntity[];
}
