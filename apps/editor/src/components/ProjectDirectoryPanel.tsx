import { useEffect, useState } from "react";
import { tr } from "../i18n";
import { useEditor, showError } from "../store/editor";
import { loadProjects, refreshProject } from "../store/actions";
import { flushPendingEdits } from "../lib/pendingEdits";
import { invalidateProject } from "../lib/api";
import { clearSessionAssets } from "../lib/assetStore";
import { directoryPath, getProjectDirectory, type ProjectDirectory, type MigrationPlan } from "../lib/projectDirectories";
import { previewRequest } from "../lib/previewPreparation";

export function ProjectDirectoryPanel() {
  const { state, dispatch } = useEditor();
  const id = state.currentId;
  const [info, setInfo] = useState<ProjectDirectory | null>(null);
  const [plan, setPlan] = useState<MigrationPlan | null>(null);
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [rollback, setRollback] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setInfo(null); setPlan(null); setRollback(false);
    if (id) void getProjectDirectory(id).then(value => { if (!cancelled) setInfo(value); })
      .catch(error => { if (!cancelled) showError(dispatch, error); });
    return () => { cancelled = true; };
  }, [id, dispatch]);
  if (!id || !info) return null;
  const act = async (operation: "plan" | "migrate" | "rollback") => {
    if (!await flushPendingEdits()) return;
    setBusy(true);
    try {
      const path = directoryPath(id);
      if (operation === "plan") {
        const result = await previewRequest<MigrationPlan>(`${path}/plan`, undefined, {});
        setPlan(result); setTarget(result.suggestedDirectory);
      } else {
        await previewRequest(`${path}/${operation}`, undefined,
          operation === "migrate" ? { token: plan?.token, directory: target.trim() } : {});
        invalidateProject(id); clearSessionAssets();
        setInfo(await getProjectDirectory(id)); setPlan(null); setRollback(operation === "migrate");
        await loadProjects(dispatch); await refreshProject(dispatch, id);
        dispatch({ type: "STATUS_SET", severity: "ok", text: tr("工程存储位置已更新，原文件已保留。") });
        // Re-fetch the scoped asset ledger without losing the current selection.
        window.dispatchEvent(new Event("cutvoke-project-media-refresh"));
      }
    } catch (error) { showError(dispatch, error); }
    finally { setBusy(false); }
  };
  return <section className="project-directory" aria-label={tr("工程目录")} style={{ marginTop: 12, display: "grid", gap: 8 }}>
    <strong>{tr("工程目录")}</strong>
    {info.directory ? <>
      <code style={{ overflowWrap: "anywhere", fontSize: 11 }}>{info.directory}</code>
      <p className="cv-hint">{tr("关闭应用后移动或备份整个目录，再从首页打开。")}</p>
      {rollback && <button className="cv-btn" disabled={busy || !!state.editLock} onClick={() => void act("rollback")}>{tr("回退迁移")}</button>}
    </> : <>
      <button className="cv-btn" disabled={busy || !!state.editLock} onClick={() => void act("plan")}>{tr("预览迁移到独立目录")}</button>
      {plan && <>
        <p className="cv-hint">{tr("复制 {0} 个文件（{1} MiB），保留 {2} 条历史快照。", plan.fileCount, (plan.copyBytes / 1024 ** 2).toFixed(1), plan.historySnapshots)}</p>
        <input className="cv-input" aria-label={tr("迁移目标目录")} value={target} disabled={busy} onChange={event => setTarget(event.target.value)} />
        <p className="cv-hint">{tr("迁移后尚未编辑时可回退；原库和原素材始终保留。")}</p>
        {!!plan.missing.length && <p role="alert" className="cv-hint">{tr("存在缺失素材，请先重新链接。")}
          <span style={{ display: "block", overflowWrap: "anywhere" }}>{plan.missing.join("\n")}</span></p>}
        <button className="cv-btn cv-btn--primary" disabled={busy || !plan.canMigrate || !target.trim() || !!state.editLock}
          onClick={() => void act("migrate")}>{busy ? tr("处理中…") : tr("迁移到独立目录")}</button>
      </>}
    </>}
  </section>;
}
