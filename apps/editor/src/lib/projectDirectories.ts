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
