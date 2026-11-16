import { previewRequest } from "./previewPreparation";
export interface ProjectDirectory {
  projectId: string;
  storageKind: "legacy" | "directory";
  directory: string | null;
  entryPath?: string;
  exportDirectory?: string;
}
export interface MigrationPlan {
  token: string; fileCount: number; copyBytes: number; missing: string[];
  canMigrate: boolean; historySnapshots: number; suggestedDirectory: string;
}
export const directoryPath = (id: string) => `projects/${encodeURIComponent(id)}/directory`;
export const getProjectDirectory = (id: string) => previewRequest<ProjectDirectory>(directoryPath(id));
export const openProjectDirectory = (path: string) =>
  previewRequest<{ projectId: string; name: string }>("project-directories/open", undefined, { path });

/** Inspection must be read-only: no registration, rebasing or background jobs. */
export interface ProjectFormatInspection {
  status: "legacy" | "current";
  sourceVersion: number;
  targetVersion: number;
  token: string;
  projectId: string;
  name: string;
}
export type ProjectOpenSource = { kind: "directory"; path: string } | { kind: "recent"; projectId: string } | { kind: "package"; path: string };
export interface OpenedProject { projectId: string; name: string; directory?: string; warnings?: string[] }
export function inspectProjectFormat(source: ProjectOpenSource) {
  if (source.kind === "package") return previewRequest<ProjectFormatInspection>("packages/format/inspect", undefined, { path: source.path });
  return source.kind === "directory"
    ? previewRequest<ProjectFormatInspection>("project-directories/inspect", undefined, { path: source.path })
    : previewRequest<ProjectFormatInspection>(`projects/${encodeURIComponent(source.projectId)}/format/inspect`, undefined, {});
}
export function convertProjectCopy(source: ProjectOpenSource, token: string) {
  if (source.kind === "package") return previewRequest<OpenedProject>("packages/format/convert", undefined, { path: source.path, token });
  return source.kind === "directory"
    ? previewRequest<OpenedProject>("project-directories/convert", undefined, { path: source.path, token })
    : previewRequest<OpenedProject>(`projects/${encodeURIComponent(source.projectId)}/format/convert`, undefined, { token });
}
