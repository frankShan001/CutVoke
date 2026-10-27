import { useState } from "react";
import { PanelsTopLeft } from "lucide-react";
import { tr, useLocale } from "../i18n";
import { applyWorkspace, currentWorkspace, DEFAULT_LAYOUT, persistWorkspace, savedWorkspaces } from "../lib/workspaces";

export function WorkspaceSettings() {
  useLocale();
  const [items, setItems] = useState(savedWorkspaces().items), [name, setName] = useState("");
  return <details className="workspace-settings"><summary className="tool-btn" aria-label={tr("工作区")}><PanelsTopLeft size={14} /><span className="topbar__btn-label">{tr("工作区")}</span></summary><div className="workspace-settings__menu">
    {items.map(item => <div key={item.name}><button type="button" className="cv-btn" onClick={() => applyWorkspace(item.layout)}>{item.name}</button>
      <button type="button" className="cv-btn cv-btn--sm" aria-label={tr("删除 {0}", item.name)} onClick={() => { const next = items.filter(value => value.name !== item.name); setItems(next); persistWorkspace(next); }}>{tr("删除")}</button></div>)}
    <label>{tr("工作区名称")}<input className="cv-input" value={name} maxLength={40} onChange={event => setName(event.target.value)} /></label>
    <button type="button" className="cv-btn" disabled={!name.trim() || items.length >= 8 && !items.some(item => item.name === name.trim())} onClick={() => {
      const next = [...items.filter(item => item.name !== name.trim()), { name: name.trim(), layout: currentWorkspace() }]; setItems(next); persistWorkspace(next); setName("");
    }}>{tr("保存当前布局")}</button>
    <button type="button" className="cv-btn" onClick={() => applyWorkspace(DEFAULT_LAYOUT)}>{tr("重置布局")}</button>
  </div></details>;
}
