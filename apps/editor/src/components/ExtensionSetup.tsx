import { useEffect, useRef, useState } from "react";
import { tr, useLocale } from "../i18n";
import { previewRequest } from "../lib/previewPreparation";
import "../styles/extensions.css";

type ExtensionId = "person" | "object";
type SourceMode = "auto" | "china" | "global" | "offline";
interface Receipt { confirmationId: string; decision: "accept" | "decline" }
interface InstallJob { jobId: string; extensionId: ExtensionId; status: string; phase: string; progress: number; error?: string; source?: string; filename?: string; retryable?: boolean }
interface Extension { id: ExtensionId; installed: boolean; job?: InstallJob | null }
interface Plan {
  planId: string; extensionId: ExtensionId; installable: boolean; alreadyInstalled: boolean;
  installDirectory: string; downloadBytes: number; installedBytes: number; state: string;
  disk: { freeBytes: number; requiredBytes: number; sufficient: boolean };
  device: { suitability: string; backend?: string; ramBytes: number; gpus: { name: string }[] };
  confirmation?: Receipt | null;
}
const running = (job?: InstallJob | null) => !!job && ["queued", "running", "cancelling"].includes(job.status);
const bytes = (value: number) => value < 1024 ** 3 ? `${(value / 1024 ** 2).toFixed(1)} MiB` : `${(value / 1024 ** 3).toFixed(2)} GiB`;
function title(id: ExtensionId) { return id === "person" ? tr("人物抠像") : tr("物体跟踪抠像"); }
function phase(job: InstallJob) {
  if (job.status === "completed") return tr("安装完成，可以使用");
  if (job.status === "cancelled") return tr("已取消，下载文件已保留");
  if (job.status === "interrupted") return tr("安装已中断，可以继续");
  if (job.status === "failed") return tr("安装未完成，请重试");
  if (job.status === "cancelling") return tr("正在取消…");
  if (job.phase === "installing") return tr("正在安装组件…");
  if (job.phase === "verifying") return tr("正在验证模型能否运行…");
  if (job.phase === "switching_source") return tr("正在切换下载源…");
  if (job.phase === "downloading") return tr("正在下载组件…");
  return tr("正在检查下载源…");
}

export function ExtensionSetup({ onReady }: { onReady?: () => void }) {
  useLocale();
  const [extensions, setExtensions] = useState<Extension[]>([]);
  const [selected, setSelected] = useState<ExtensionId | null>(null);
  const [mode, setMode] = useState<SourceMode>("auto");
  const [directory, setDirectory] = useState("");
  const [offline, setOffline] = useState("");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const alive = useRef(false);
  const callback = useRef(onReady); callback.current = onReady;
  const controllers = useRef(new Set<AbortController>());
  const readyIds = useRef("");
  const review = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const request = async <T,>(action: string, body?: object) => {
    const controller = new AbortController(); controllers.current.add(controller);
    try { return await previewRequest<T>(`extensions/${action}`, controller.signal, body); }
    finally { controllers.current.delete(controller); }
  };
  const refresh = async () => {
    const result = await request<{ extensions: Extension[] }>("status");
    if (!alive.current) return;
    setExtensions(result.extensions);
    const ids = result.extensions.filter(value => value.installed).map(value => value.id).sort().join(",");
    if (ids !== readyIds.current) { readyIds.current = ids; callback.current?.(); }
  };
  const run = async (operation: () => Promise<void>) => {
    setBusy(true); setError("");
    try { await operation(); }
    catch (failure) { if (alive.current && !(failure instanceof DOMException && failure.name === "AbortError")) setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { if (alive.current) setBusy(false); }
  };
  useEffect(() => {
    alive.current = true; void run(refresh);
    return () => { alive.current = false; controllers.current.forEach(controller => controller.abort()); };
  }, []);
  // Shared server jobs survive panel closure and can also be started by MCP.
  useEffect(() => {
    let polling = false;
    const timer = window.setInterval(() => {
      if (polling || !alive.current) return;
      polling = true;
      void refresh().catch(failure => { if (alive.current) setError(String(failure)); }).finally(() => { polling = false; });
    }, extensions.some(value => running(value.job)) ? 1000 : 5000);
    return () => window.clearInterval(timer);
  }, [extensions.some(value => running(value.job))]);
  useEffect(() => { if (selected) review.current?.focus(); }, [selected]);
  const close = () => { setSelected(null); setPlan(null); trigger.current?.focus(); };
  const change = () => { setPlan(null); setError(""); };
  const inspect = async () => {
    if (!selected) return;
    const result = await request<{ plan: Plan }>("plan", { extensionId: selected, sourceMode: mode,
      ...(directory.trim() ? { installDirectory: directory.trim() } : {}), ...(mode === "offline" ? { offlinePath: offline.trim() } : {}) });
    if (alive.current) { setPlan(result.plan); setDirectory(result.plan.installDirectory); }
  };
  const decide = async (accept: boolean) => {
    if (!plan) return;
    const receipt = accept && plan.confirmation?.decision === "accept"
      ? { confirmation: plan.confirmation }
      : await request<{ confirmation: Receipt }>("confirm", { planId: plan.planId,
        decision: accept ? "accept" : "decline", channel: "ui", userResponse: accept ? tr("同意并安装") : tr("暂不安装") });
    if (accept) await request("install", { planId: plan.planId, confirmationId: receipt.confirmation.confirmationId });
    if (alive.current) { await refresh(); close(); }
  };
  return <section className="extension-setup" aria-label={tr("可选抠像组件")}>
    <p className="extension-setup__intro">{tr("按需启用，在本机处理视频。首次使用需下载组件，普通剪辑无需安装。")}</p>
    {busy && extensions.length === 0 && <p role="status">{tr("正在检查…")}</p>}
    {error && <div role="alert"><strong>{tr("组件操作未完成")}</strong><p dir="auto">{error}</p><button className="tool-btn" disabled={busy} onClick={() => void run(refresh)}>{tr("刷新")}</button></div>}
    {extensions.map(extension => <article className="extension-setup__card" key={extension.id}>
      <div><strong>{title(extension.id)}</strong><span>{extension.installed ? tr("已就绪") : tr("未安装")}</span></div>
      <p>{extension.id === "person" ? tr("自动移除人物背景，也可点选目标。") : tr("跟踪选定的人物或物体，适合移动和遮挡镜头。")}</p>
      {extension.job && <div className="extension-setup__job" role="status">
        <span>{phase(extension.job)}</span>
        {running(extension.job) && <progress aria-label={tr("组件安装进度")} value={extension.job.progress} max={100} />}
        {extension.job.error && <details><summary>{tr("查看详情")}</summary><p dir="auto">{extension.job.error}</p></details>}
        {running(extension.job) && <button className="tool-btn" disabled={busy || extension.job.status === "cancelling"} onClick={() => void run(async () => { await request("job", { jobId: extension.job!.jobId, operation: "cancel" }); await refresh(); })}>{tr("取消安装")}</button>}
      </div>}
      {!extension.installed && !running(extension.job) && <button className="tool-btn" disabled={busy || extensions.some(value => running(value.job))} onClick={event => { trigger.current = event.currentTarget; setSelected(extension.id); change(); }}>{extension.job?.retryable ? tr("继续安装") : tr("启用")}</button>}
    </article>)}
    {selected && <div ref={review} tabIndex={-1} className="extension-setup__review" role="group" aria-label={tr("确认组件安装")}>
      <h4>{tr("启用{0}", title(selected))}</h4>
      <p>{tr("视频留在本机。处理速度和边缘效果取决于设备与画面，建议先试一小段。")}</p>
      <details><summary>{tr("下载和安装位置")}</summary>
        <label>{tr("下载源")}<select value={mode} disabled={busy} onChange={event => { setMode(event.target.value as SourceMode); change(); }}>
          <option value="auto">{tr("自动选择可用来源")}</option><option value="china">{tr("优先大陆来源")}</option><option value="global">{tr("优先海外来源")}</option><option value="offline">{tr("离线安装包")}</option>
        </select></label>
        <label>{tr("组件保存目录")}<input value={directory} disabled={busy} onChange={event => { setDirectory(event.target.value); change(); }} /></label>
        {window.cutvokeDesktop && <button className="tool-btn" disabled={busy} onClick={() => void run(async () => { const chosen = await window.cutvokeDesktop!.chooseExtensionDirectory(); if (chosen && alive.current) { setDirectory(chosen); change(); } })}>{tr("选择目录")}</button>}
        {mode === "offline" && <><label>{tr("离线安装包")}<input value={offline} disabled={busy} onChange={event => { setOffline(event.target.value); change(); }} /></label>
          {window.cutvokeDesktop && <button className="tool-btn" disabled={busy} onClick={() => void run(async () => { const chosen = await window.cutvokeDesktop!.chooseExtensionBundle(); if (chosen && alive.current) { setOffline(chosen); change(); } })}>{tr("选择文件")}</button>}</>}
      </details>
      <button className="tool-btn" disabled={busy || mode === "offline" && !offline.trim()} onClick={() => void run(inspect)}>{busy ? tr("正在检查…") : tr("检查设备与空间")}</button>
      {plan && <div role="status">
        <p>{plan.device.suitability === "unsupported" ? tr("此系统暂未提供经过验证的安装包。") : plan.device.suitability === "not_recommended" ? tr("设备内存不足，暂不建议启用此组件。") : plan.device.suitability === "slow" ? tr("可以运行，但处理可能较慢。建议先试一小段。") : tr("设备满足基本运行条件，实际速度请以短片试用为准。")}</p>
        <dl><dt>{tr("需要下载")}</dt><dd>{bytes(plan.downloadBytes)}</dd><dt>{tr("安装后约占用")}</dt><dd>{bytes(plan.installedBytes)}</dd><dt>{tr("安装需预留空间")}</dt><dd>{bytes(plan.disk.requiredBytes)}</dd><dt>{tr("磁盘可用空间")}</dt><dd>{bytes(plan.disk.freeBytes)}</dd></dl>
        {!plan.disk.sufficient && <p className="extension-setup__error">{tr("空间不足，请选择其他磁盘后重新检查。")}</p>}
        <p className="extension-setup__path" dir="auto">{plan.installDirectory}</p>
        {plan.installable && !plan.alreadyInstalled && <div className="extension-setup__actions">
          <button className="tool-btn tool-btn--primary" disabled={busy} onClick={() => void run(() => decide(true))}>{plan.confirmation?.decision === "accept" ? tr("继续安装") : tr("同意并安装")}</button>
          <button className="tool-btn" disabled={busy} onClick={() => void run(() => decide(false))}>{tr("暂不安装")}</button>
        </div>}
      </div>}
      <button className="tool-btn" disabled={busy} onClick={close}>{tr("关闭")}</button>
    </div>}
  </section>;
}
