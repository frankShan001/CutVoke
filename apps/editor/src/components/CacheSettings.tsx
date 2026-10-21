import { tr, useLocale } from "../i18n";
import { useEffect, useRef, useState } from "react";
import { Settings, FolderOpen, RefreshCw, X } from "lucide-react";
import { previewRequest } from "../lib/previewPreparation";
import { getLanguagePreference, LANGUAGE_NAMES, LOCALES, setLanguage, type LanguagePreference } from "../i18n/locale";
import type { DesktopSettings } from "../types/desktop";
import "../styles/cache-settings.css";
import { ResourcePackManager } from "./ResourcePackManager";
import "../styles/resources.css";

interface CacheStatus {
  directory: string; maxBytes: number; usedBytes: number; freeBytes: number;
  entries: number; overBudget: boolean;
  categories: { kind: string; entries: number; bytes: number }[];
}
const gib = (bytes: number) => (bytes / 1024 ** 3).toFixed(2);
const readableBytes = (bytes: number) => bytes < 1024 ** 3 ? `${(bytes / 1024 ** 2).toFixed(1)} MiB` : `${gib(bytes)} GiB`;
const kinds: Record<string, string> = { get window() { return tr("片段预览"); }, get prepared() { return tr("连续预览"); }, get frame() { return tr("定位帧"); }, get proxy() { return tr("代理素材"); }, get preview() { return tr("工程预览"); } };

export function CacheSettings() {
  useLocale();
  const [open, setOpen] = useState(false);
  const [section, setSection] = useState<"general" | "resources">("general");
  const [resourceBusy, setResourceBusy] = useState(false);
  const [status, setStatus] = useState<CacheStatus | null>(null);
  const [directory, setDirectory] = useState("");
  const [capacity, setCapacity] = useState("10");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [confirmClear, setConfirmClear] = useState(false);
  const [mcpConfig, setMcpConfig] = useState("");
  const [desktopSettings, setDesktopSettings] = useState<DesktopSettings | null>(null);
  const [projectDirectory, setProjectDirectory] = useState("");
  const [firstRun, setFirstRun] = useState(false);
  const [migrationConfirmed, setMigrationConfirmed] = useState(false);
  const desktop = window.cutvokeDesktop;
  const moving = !!desktopSettings && projectDirectory.trim() !== desktopSettings.projectDirectory;
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const load = async () => {
    if (desktop) {
      const settings = await desktop.settings();
      if (!alive.current) return;
      setDesktopSettings(settings); setProjectDirectory(settings.projectDirectory);
      setFirstRun(!settings.setupComplete);
    }
    const current = await previewRequest<CacheStatus>("preview-cache");
    if (!alive.current) return;
    setStatus(current); setDirectory(current.directory); setCapacity(String(current.maxBytes / 1024 ** 3));
  };
  useEffect(() => {
    if (!desktop) return;
    void desktop.settings().then(settings => {
      if (alive.current && !settings.setupComplete) { setFirstRun(true); setOpen(true); }
    }).catch(() => { if (alive.current) setOpen(true); });
  }, [desktop]);
  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError(""); setMessage("");
    try { await action(); } catch (failure) {
      if (alive.current) setError(failure instanceof Error ? failure.message : tr("缓存设置失败，请重试"));
    } finally { if (alive.current) setBusy(false); }
  };
  useEffect(() => {
    if (!open) return;
    setStatus(null); setError(""); setMigrationConfirmed(false); setSection("general");
    dialog.current?.showModal();
    void run(load);
    return () => { dialog.current?.close(); };
  }, [open]); // Read settings when the dialog opens, never on playback ticks.
  const close = () => {
    if (busy || resourceBusy || firstRun) return;
    setOpen(false); setConfirmClear(false); setMigrationConfirmed(false); trigger.current?.focus();
  };
  const save = async () => {
    if (!directory.trim()) throw new Error(tr("请填写缓存保存目录"));
    const amount = Number(capacity);
    if (!Number.isFinite(amount) || amount < .25 || amount > 1024) throw new Error(tr("缓存容量需要在 0.25 到 1024 GiB 之间"));
    const maxBytes = Math.round(amount * 1024 ** 3);
    if (desktop) {
      if (!projectDirectory.trim()) throw new Error(tr("请选择工程保存位置"));
      if (moving && !migrationConfirmed) throw new Error(tr("请先确认迁移说明"));
      const result = await desktop.saveSettings({ projectDirectory: projectDirectory.trim(), cacheDirectory: directory, maxBytes });
      if (result.restartRequired) { setMessage(tr("设置已保存，正在重新打开应用…")); return; }
    } else {
      await previewRequest("preview-cache", undefined, { directory, maxBytes });
    }
    const wasFirstRun = firstRun;
    await load();
    setMessage(tr("设置已保存"));
    if (wasFirstRun) { setFirstRun(false); setOpen(false); }
  };
  return <>
    <button ref={trigger} className="tool-btn cache-settings-trigger" aria-label={tr("设置")} title={tr("语言、工程与缓存设置")} onClick={() => { setMessage(""); setOpen(true); }}>
      <Settings size={14} /><span>{tr("设置")}</span>
    </button>
    {open && <dialog ref={dialog} className="cache-settings" aria-labelledby="cache-settings-title" onCancel={event => { event.preventDefault(); close(); }}>
      <header><div><h2 id="cache-settings-title">{firstRun ? tr("欢迎使用 CutVoke") : tr("设置")}</h2>
        <p>{firstRun ? tr("先确认语言和保存位置，之后可随时在设置中修改。") : tr("管理界面语言、工程保存位置和预览缓存。")}</p></div>
        {!firstRun && <button className="tool-btn" disabled={busy || resourceBusy} aria-label={tr("关闭设置")} onClick={close}><X size={18} /></button>}</header>
      {!firstRun && <div className="settings-navigation" role="group" aria-label={tr("设置")}>
        <button type="button" className="tool-btn" aria-pressed={section === "general"} disabled={busy || resourceBusy} onClick={() => setSection("general")}>{tr("常规")}</button>
        <button type="button" className="tool-btn" aria-pressed={section === "resources"} disabled={busy || resourceBusy} onClick={() => setSection("resources")}>{tr("资源管理")}</button>
      </div>}
      {section === "resources" && !firstRun && <ResourcePackManager onBusyChange={setResourceBusy} />}
      <div hidden={section !== "general"}>
      {message && <p role="status" className="cache-settings__success">{message}</p>}
      {error && <p role="alert" className="cache-settings__error">{error}</p>}
      <label>{tr("界面语言")}<select aria-label={tr("设置中的界面语言")} value={getLanguagePreference()} disabled={busy}
        onChange={event => setLanguage(event.target.value as LanguagePreference)}>
        <option value="system">{tr("跟随系统")}</option>
        {LOCALES.map(locale => <option key={locale} value={locale} lang={locale} dir={locale === "ar" ? "rtl" : "ltr"}>{LANGUAGE_NAMES[locale]}</option>)}
      </select></label>
      <p className="cache-settings__muted">{tr("语言切换立即生效。")}</p>
      {busy && !status ? <p role="status">{tr("正在读取缓存…")}</p> : null}
      {status && <>
        <section className="settings-section" aria-label={tr("工程保存位置")}>
          <h3>{tr("工程保存位置")}</h3>
          {desktop ? <>
            <label>{tr("工程库目录")}<div className="cache-settings__directory">
              <input value={projectDirectory} disabled={busy || desktopSettings?.projectLocationManaged}
                onChange={event => { setProjectDirectory(event.target.value); setMigrationConfirmed(false); }} />
              <button className="tool-btn" disabled={busy || desktopSettings?.projectLocationManaged}
                onClick={() => void run(async () => { const selected = await desktop.chooseProjectDirectory(); if (selected) { setProjectDirectory(selected); setMigrationConfirmed(false); } })}>{tr("选择")}</button>
            </div></label>
            <p className="cache-settings__muted">{desktopSettings?.projectLocationManaged
              ? tr("此位置由启动参数 CUTVOKE_DATA 指定。修改启动配置后重新打开应用即可更改。")
              : tr("工程和导入的素材保存在此处。更换位置会复制现有数据并重新打开应用，原目录保留。")}</p>
            {moving && <label className="settings-confirm"><input type="checkbox" checked={migrationConfirmed} disabled={busy}
              onChange={event => setMigrationConfirmed(event.target.checked)} />
              <span>{tr("目标目录必须为空。我已停止外部 Agent 和其他编辑服务，迁移后会更新其连接配置。")}</span></label>}
          </> : <p className="cache-settings__muted">{tr("浏览器版的工程位置由本地服务管理。请在桌面版设置中修改，或调整服务的 --data 启动参数。")}</p>}
        </section>
        <section className="settings-section" aria-label={tr("预览缓存")}>
        <h3>{tr("预览缓存")}</h3>
        <p className="cache-settings__muted">{tr("保留已准备的画面，再次打开工程时复用。")}</p>
        {!firstRun && <><div className="cache-settings__usage"><strong>{readableBytes(status.usedBytes)} <small>{tr("已使用")}</small></strong><span>{tr("上限 ")}{gib(status.maxBytes)} GiB · {status.entries}{tr(" 个缓存文件")}</span></div>
        <meter aria-label={tr("缓存使用量")} min={0} max={status.maxBytes} value={Math.min(status.usedBytes, status.maxBytes)} />
        <p className="cache-settings__muted">{tr("所在磁盘剩余 ")}{gib(status.freeBytes)} GiB{status.overBudget ? tr(" · 正在使用的缓存暂时保留，空闲后回收") : ""}</p>
        <div className="cache-settings__categories">{status.categories.map(item => <span key={item.kind}>{kinds[item.kind] || item.kind}<b>{readableBytes(item.bytes)}</b></span>)}</div></>}
        <label>{tr("缓存容量（GiB）")}<input type="number" min="0.25" max="1024" step="0.25" value={capacity} disabled={busy} onChange={e => setCapacity(e.target.value)} /></label>
        <label>{tr("保存目录")}<div className="cache-settings__directory"><input value={directory} disabled={busy} onChange={e => setDirectory(e.target.value)} />
          {window.cutvokeDesktop && <button className="tool-btn" disabled={busy} onClick={() => void run(async () => { const selected = await window.cutvokeDesktop!.chooseCacheDirectory(); if (selected) setDirectory(selected); })}><FolderOpen size={14} />{tr("选择")}</button>}</div></label>
        <p className="cache-settings__muted">{tr("更换目录后会在新位置保存缓存；原位置的缓存保留。清理缓存后，后续访问会重新准备画面。")}</p>
        {!firstRun && <div className="cache-settings__actions">
          <button className="tool-btn" disabled={busy} onClick={() => void run(async () => setStatus(await previewRequest<CacheStatus>("preview-cache")))}><RefreshCw size={14} />{tr("刷新用量")}</button>
          {window.cutvokeDesktop && <button className="tool-btn" disabled={busy} onClick={() => void run(() => window.cutvokeDesktop!.revealCacheDirectory())}>{tr("打开目录")}</button>}
        </div>}
        </section>
        {!firstRun && <><footer><div><strong>{tr("清理空闲缓存")}</strong><p>{tr("正在使用和最近访问的缓存会保留。")}</p></div>
          {!confirmClear ? <button className="tool-btn" disabled={busy} onClick={() => setConfirmClear(true)}>{tr("清理")}</button> : <div className="cache-settings__actions">
            <button className="tool-btn" disabled={busy} onClick={() => setConfirmClear(false)}>{tr("取消")}</button>
            <button className="tool-btn" disabled={busy} onClick={() => void run(async () => {
              const result = await previewRequest<{ freedBytes: number }>("preview-cache/clear", undefined, {});
              await load(); setConfirmClear(false); setMessage(tr("已释放 {0} GiB", gib(result.freedBytes)));
            })}>{tr("确认清理")}</button></div>}
        </footer>
        {window.cutvokeDesktop && <button className="tool-btn" disabled={busy} onClick={() => void run(async () => {
          const info = await window.cutvokeDesktop!.info(); setMcpConfig(JSON.stringify(info.mcp, null, 2));
          setMessage(tr("配置已生成，选中文字后按 Ctrl+C 复制"));
        })}>{tr("查看客户端 MCP 配置")}</button>}
        {mcpConfig && <label>{tr("客户端 MCP 配置")}<textarea aria-label={tr("客户端 MCP 配置")} rows={7} readOnly value={mcpConfig} onFocus={event => event.currentTarget.select()} /></label>}</>}
        <div className="settings-save"><button className="tool-btn tool-btn--primary" disabled={busy || (moving && !migrationConfirmed)}
          onClick={() => void run(save)}>{busy ? tr("正在保存…") : moving ? tr("迁移并重新打开") : firstRun ? tr("保存并开始使用") : tr("保存设置")}</button></div>
      </>}
      {!status && !busy && <button className="tool-btn" onClick={() => void run(load)}>{tr("重试")}</button>}
      </div>
    </dialog>}
  </>;
}
