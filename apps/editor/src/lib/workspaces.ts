export interface WorkspaceLayout { left: boolean; right: boolean; leftWidth: number; rightWidth: number; timelineHeight: number }
export interface SavedWorkspace { name: string; layout: WorkspaceLayout }
const KEY = "cutvoke.workspaces.v1";
export const DEFAULT_LAYOUT: WorkspaceLayout = { left: true, right: true, leftWidth: 260, rightWidth: 300, timelineHeight: 300 };
export function validLayout(value: unknown): WorkspaceLayout {
  const item = value as Partial<WorkspaceLayout> | null;
  const bounded = (value: unknown, fallback: number, low: number, high: number) => typeof value === "number" && Number.isFinite(value) ? Math.max(low, Math.min(high, value)) : fallback;
  return { left: typeof item?.left === "boolean" ? item.left : true, right: typeof item?.right === "boolean" ? item.right : true,
    leftWidth: bounded(item?.leftWidth, 260, 200, 520), rightWidth: bounded(item?.rightWidth, 300, 240, 560), timelineHeight: bounded(item?.timelineHeight, 300, 120, 900) };
}
export function savedWorkspaces(): { items: SavedWorkspace[]; active?: WorkspaceLayout } {
  try { const data = JSON.parse(localStorage.getItem(KEY) || "{}");
    return { items: Array.isArray(data.items) ? data.items.filter((item: SavedWorkspace) => typeof item.name === "string").slice(0, 8)
      .map((item: SavedWorkspace) => ({ name: item.name.slice(0, 40), layout: validLayout(item.layout) })) : [], active: data.active ? validLayout(data.active) : undefined };
  } catch { return { items: [] }; }
}
export function persistWorkspace(items: SavedWorkspace[], active?: WorkspaceLayout) {
  try { localStorage.setItem(KEY, JSON.stringify({ items: items.slice(0, 8), active: active || savedWorkspaces().active })); } catch { /* Layout remains usable when browser storage is unavailable. */ }
}
export function applyWorkspace(layout: WorkspaceLayout) {
  const value = validLayout(layout);
  for (const [key, amount] of [["--zone-left-w", value.leftWidth], ["--zone-right-w", value.rightWidth], ["--timeline-h", value.timelineHeight]] as const)
    document.documentElement.style.setProperty(key, `${amount}px`);
  persistWorkspace(savedWorkspaces().items, value);
  window.dispatchEvent(new CustomEvent("cutvoke:workspace", { detail: value }));
  window.dispatchEvent(new Event("resize"));
}
export function currentWorkspace(): WorkspaceLayout {
  const style = getComputedStyle(document.documentElement);
  return validLayout({ left: !document.querySelector(".zone-left--collapsed"), right: !document.querySelector(".zone-right--collapsed"),
    leftWidth: parseFloat(style.getPropertyValue("--zone-left-w")), rightWidth: parseFloat(style.getPropertyValue("--zone-right-w")),
    timelineHeight: parseFloat(style.getPropertyValue("--timeline-h")) });
}
export function rememberWorkspace() { persistWorkspace(savedWorkspaces().items, currentWorkspace()); }
