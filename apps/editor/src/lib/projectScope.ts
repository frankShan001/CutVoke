/** Media requests retain the project selected when the operation started. */
let currentProject: string | null = null;
export const activeProjectId = () => currentProject;
export function setActiveProject(projectId: string): boolean {
  if (currentProject === projectId) return false;
  currentProject = projectId;
  return true;
}
export function projectMediaUrl(url: string, projectId = currentProject): string {
  if (!projectId) return url;
  return `${url}${url.includes("?") ? "&" : "?"}projectId=${encodeURIComponent(projectId)}`;
}
