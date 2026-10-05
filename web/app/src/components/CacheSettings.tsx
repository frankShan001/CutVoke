import { useEffect, useRef, useState } from "react";
import { Database, FolderOpen, RefreshCw, X } from "lucide-react";
import { previewRequest } from "../lib/previewPreparation";
import "../styles/cache-settings.css";

interface CacheStatus {
  directory: string; maxBytes: number; usedBytes: number; freeBytes: number;
  entries: number; overBudget: boolean;
  categories: { kind: string; entries: number; bytes: number }[];
}
declare global {
  interface Window {
    cutvokeDesktop?: {
      chooseCacheDirectory(): Promise<string | null>;
      revealCacheDirectory(): Promise<void>;
      info(): Promise<{ mcp: object; version: string }>;
    };
  }
}
const gib = (bytes: number) => (bytes / 1024 ** 3).toFixed(2);
const readableBytes = (bytes: number) => bytes < 1024 ** 3 ? `${(bytes / 1024 ** 2).toFixed(1)} MiB` : `${gib(bytes)} GiB`;
const kinds: Record<string, string> = { window: "片段预览", prepared: "连续预览", frame: "定位帧", proxy: "代理素材", preview: "工程预览" };

export function CacheSettings() {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<CacheStatus | null>(null);
  const [directory, setDirectory] = useState("");
  const [capacity, setCapacity] = useState("10");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [confirmClear, setConfirmClear] = useState(false);
  const [mcpConfig, setMcpConfig] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const load = async () => {
    const current = await previewRequest<CacheStatus>("preview-cache");
    if (!alive.current) return;
    setStatus(current); setDirectory(current.directory); setCapacity(String(current.maxBytes / 1024 ** 3));
  };
  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError(""); setMessage("");
    try { await action(); } catch (failure) {
      if (alive.current) setError(failure instanceof Error ? failure.message : "缓存设置失败，请重试");
    } finally { if (alive.current) setBusy(false); }
  };
  useEffect(() => {
    if (!open) return;
    dialog.current?.showModal();
    void run(load);
    return () => { dialog.current?.close(); };
  }, [open]); // Read settings when the dialog opens, never on playback ticks.
  const close = () => { setOpen(false); setConfirmClear(false); trigger.current?.focus(); };
  return <>
    <button ref={trigger} className="tool-btn cache-settings-trigger" aria-label="缓存设置" title="缓存容量与保存位置" onClick={() => setOpen(true)}>
      <Database size={14} /><span>缓存</span>
    </button>
    {open && <dialog ref={dialog} className="cache-settings" aria-labelledby="cache-settings-title" onCancel={close}>
      <header><div><h2 id="cache-settings-title">预览缓存</h2><p>保留已准备的画面，再次打开工程时复用。</p></div>
        <button className="tool-btn" aria-label="关闭缓存设置" onClick={close}><X size={18} /></button></header>
      {busy && !status ? <p role="status">正在读取缓存…</p> : null}
      {status && <>
        <div className="cache-settings__usage"><strong>{readableBytes(status.usedBytes)} <small>已使用</small></strong><span>上限 {gib(status.maxBytes)} GiB · {status.entries} 个缓存文件</span></div>
        <meter aria-label="缓存使用量" min={0} max={status.maxBytes} value={Math.min(status.usedBytes, status.maxBytes)} />
        <p className="cache-settings__muted">所在磁盘剩余 {gib(status.freeBytes)} GiB{status.overBudget ? " · 正在使用的缓存暂时保留，空闲后回收" : ""}</p>
        <div className="cache-settings__categories">{status.categories.map(item => <span key={item.kind}>{kinds[item.kind] || item.kind}<b>{readableBytes(item.bytes)}</b></span>)}</div>
        <label>缓存容量（GiB）<input type="number" min="0.25" max="1024" step="0.25" value={capacity} disabled={busy} onChange={e => setCapacity(e.target.value)} /></label>
        <label>保存目录<div className="cache-settings__directory"><input value={directory} disabled={busy} onChange={e => setDirectory(e.target.value)} />
          {window.cutvokeDesktop && <button className="tool-btn" disabled={busy} onClick={() => void run(async () => { const selected = await window.cutvokeDesktop!.chooseCacheDirectory(); if (selected) setDirectory(selected); })}><FolderOpen size={14} />选择</button>}</div></label>
        <p className="cache-settings__muted">更换目录后会在新位置保存缓存；原位置的缓存保留。清理缓存后，后续访问会重新准备画面。</p>
        <div className="cache-settings__actions">
          <button className="tool-btn" disabled={busy} onClick={() => void run(load)}><RefreshCw size={14} />刷新用量</button>
          {window.cutvokeDesktop && <button className="tool-btn" disabled={busy} onClick={() => void run(() => window.cutvokeDesktop!.revealCacheDirectory())}>打开目录</button>}
          <button className="tool-btn tool-btn--primary" disabled={busy} onClick={() => void run(async () => {
            if (!directory.trim()) throw new Error("请填写缓存保存目录");
            const amount = Number(capacity);
            if (!Number.isFinite(amount) || amount < .25 || amount > 1024) throw new Error("缓存容量需要在 0.25 到 1024 GiB 之间");
            await previewRequest("preview-cache", undefined, { directory, maxBytes: Math.round(amount * 1024 ** 3) });
            await load(); setMessage("缓存设置已保存");
          })}>保存设置</button>
        </div>
        <footer><div><strong>清理空闲缓存</strong><p>正在使用和最近访问的缓存会保留。</p></div>
          {!confirmClear ? <button className="tool-btn" disabled={busy} onClick={() => setConfirmClear(true)}>清理</button> : <div className="cache-settings__actions">
            <button className="tool-btn" disabled={busy} onClick={() => setConfirmClear(false)}>取消</button>
            <button className="tool-btn" disabled={busy} onClick={() => void run(async () => {
              const result = await previewRequest<{ freedBytes: number }>("preview-cache/clear", undefined, {});
              await load(); setConfirmClear(false); setMessage(`已释放 ${gib(result.freedBytes)} GiB`);
            })}>确认清理</button></div>}
        </footer>
        {window.cutvokeDesktop && <button className="tool-btn" disabled={busy} onClick={() => void run(async () => {
          const info = await window.cutvokeDesktop!.info(); setMcpConfig(JSON.stringify(info.mcp, null, 2));
          setMessage("配置已生成，选中文字后按 Ctrl+C 复制");
        })}>查看客户端 MCP 配置</button>}
        {mcpConfig && <label>客户端 MCP 配置<textarea aria-label="客户端 MCP 配置" rows={7} readOnly value={mcpConfig} onFocus={event => event.currentTarget.select()} /></label>}
      </>}
      {message && <p role="status" className="cache-settings__success">{message}</p>}
      {error && <p role="alert" className="cache-settings__error">{error}</p>}
    </dialog>}
  </>;
}
