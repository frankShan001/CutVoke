import { msg, tr, useLocale } from "../i18n";
/** Agent 活动抽屉（D10）：拉取事件 outbox，展示 Agent / 人工操作历史，
    支持点击改动对象跳转，以及按批次「撤销到此」。纯前端 UI，事件走 listEvents，
    撤销走现有 undo()（store/actions.ts），不触碰后端。 */

import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  ArrowRightLeft,
  Bot,
  Cpu,
  RefreshCw,
  Undo2,
  User,
  X,
  Archive,
} from "lucide-react";
import { Button } from "./ui";
import { useEditor } from "../store/editor";
import { getLatestState, undo } from "../store/actions";
import { listEvents } from "../lib/api";
import type { ChangedEntity, Project, ProjectEvent } from "../types/api";

const AGENT_KINDS = new Set(["agent", "http", "mcp", "cli"]);

const ENTITY_LABEL: Record<string, string> = {
  get clip() { return tr("片段"); },
  get track() { return tr("轨道"); },
  get caption() { return tr("字幕"); },
  get marker() { return tr("标记"); },
  get project() { return tr("工程"); },
  get asset() { return tr("素材"); },
  get effect() { return tr("效果"); },
  get transition() { return tr("转场"); },
};

const CHANGE_VERB: Record<string, string> = {
  get added() { return tr("添加"); },
  get removed() { return tr("删除"); },
  get updated() { return tr("更新"); },
  get inserted() { return tr("插入"); },
  get created() { return tr("创建"); },
};

const MAX_UNDO_STEPS = 20;

/** 判断 actor 是否为 Agent（协议中的 agent 及 http/mcp/cli 归为 Agent）。 */
function isAgentActor(actor?: { kind: string; id: string }): boolean {
  return !!actor && AGENT_KINDS.has(actor.kind);
}

/** 把一条 changedEntity 转成人类可读摘要，如「添加片段 clip_xxx」。 */
function entitySummary(e: ChangedEntity): string {
  const label = ENTITY_LABEL[e.type] || e.type;
  const verb = CHANGE_VERB[e.change] || e.change || tr("变更");
  return `${verb}${label} ${e.id}`;
}

/** 在工程的轨道里找包含某 clip 的轨道 id（用于点击跳转选中）。 */
function findClipTrack(project: Project | null, clipId: string): string | null {
  for (const t of project?.sequence.tracks || []) {
    if ((t.clips || []).some((c) => c.id === clipId)) return t.id;
  }
  return null;
}

export function AgentActivityPanel() {
  const locale = useLocale();
  const { state, dispatch } = useEditor();
  const open = state.agentPanelOpen;

  const [events, setEvents] = useState<ProjectEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastSync, setLastSync] = useState(0);
  const [busyRev, setBusyRev] = useState<string | null>(null);
  const [archiving, setArchiving] = useState(false);
  const [archivePath, setArchivePath] = useState("");

  const archive = async () => {
    const current = getLatestState();
    if (!current?.currentId || archiving) return;
    setArchiving(true); setArchivePath("");
    try {
      const response = await fetch("/api/v1/agent/project_archive", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ projectId: current.currentId, expectedRevision: current.revision,
          frameTimes: [current.playhead] }),
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error?.message || tr("制作归档失败"));
      setArchivePath(result.path);
      dispatch({ type: "STATUS_SET", severity: "ok", text: msg("制作归档已保存：{0}", result.path) });
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : tr("制作归档失败"));
    } finally { setArchiving(false); }
  };

  const load = useCallback(async () => {
    if (!state.currentId) return;
    setLoading(true);
    try {
      // 全量拉取：since=0；按 eventId 去重，按 revision 倒序（新在前）。
      const evs = await listEvents(state.currentId, "0");
      setEvents((prev) => {
        const map = new Map<string, ProjectEvent>();
        for (const e of prev) map.set(e.eventId, e);
        for (const e of evs) map.set(e.eventId, e);
        return Array.from(map.values()).sort(
          (a, b) => Number(b.revision) - Number(a.revision),
        );
      });
      setError(null);
      setLastSync(Date.now());
    } catch (err) {
      setError(err instanceof Error ? err.message : tr("事件加载失败"));
    } finally {
      setLoading(false);
    }
  }, [state.currentId]);

  // 打开时拉一次全量，之后每 5s 轮询（增量合并）。
  useEffect(() => {
    if (!open || !state.currentId) return;
    void load();
    const id = setInterval(() => void load(), 5000);
    return () => clearInterval(id);
  }, [open, state.currentId, load]);

  const close = () => dispatch({ type: "AGENT_PANEL_OPEN_SET", open: false });

  // 点击改动对象：clip → 选中（SELECTION_SET）；track → 选中；其它 → 状态提示。
  const handleNavigate = (entity: ChangedEntity) => {
    if (entity.type === "clip") {
      const trackId = findClipTrack(state.project, entity.id);
      if (trackId) {
        dispatch({ type: "SELECTION_SET", selection: { trackId, clipId: entity.id } });
        dispatch({ type: "STATUS_SET", severity: "ok", text: msg("已跳转到片段 {0}", entity.id) });
      } else {
        dispatch({
          type: "STATUS_SET",
          severity: "warn",
          text: msg("未找到片段 {0}（可能已被删除）", entity.id),
        });
      }
    } else if (entity.type === "track") {
      dispatch({ type: "SELECTION_SET", selection: { trackId: entity.id } });
      dispatch({ type: "STATUS_SET", severity: "ok", text: msg("已选中轨道 {0}", entity.id) });
    } else {
      const label = ENTITY_LABEL[entity.type] || entity.type;
      dispatch({
        type: "STATUS_SET",
        severity: "ok",
        text: msg("已定位到{0} {1}（见对应面板）", label, entity.id),
      });
    }
  };

  // 批量撤销到此活动之前的 revision（previousRevision）。
  const handleUndoTo = async (event: ProjectEvent) => {
    const target = Number(event.previousRevision || "0");
    if (busyRev === event.eventId) return;
    setBusyRev(event.eventId);
    let steps = 0;
    try {
      while (steps < MAX_UNDO_STEPS) {
        const st = getLatestState();
        const cur = Number((st?.revision) || "0");
        if (cur <= target) break;
        if (!st) break;
        const res = await undo(dispatch, st);
        if (!res.ok) {
          if (res.error?.code === "UNDO_CONFLICT") {
            dispatch({
              type: "STATUS_SET",
              severity: "warn",
              text: msg("已无可撤销内容，停止批量撤销"),
            });
          }
          break;
        }
        steps++;
      }
      if (steps >= MAX_UNDO_STEPS) {
        dispatch({
          type: "STATUS_SET",
          severity: "warn",
          text: msg("已达最大撤销步数（20），停止"),
        });
      } else {
        dispatch({
          type: "STATUS_SET",
          severity: "ok",
          text: msg("已批量撤销 {0} 步至 revision {1}", steps, target),
        });
      }
    } finally {
      setBusyRev(null);
      // 撤销改变了 revision，重新拉取事件。
      void load();
    }
  };

  if (!open) return null;

  const agentCount = events.filter((e) => isAgentActor(e.actor)).length;

  return (
    <>
      <div className="agent-overlay" onClick={close} aria-hidden="true" />
      <aside className="agent-panel" aria-label={tr("Agent 活动")}>
        <header className="agent-panel__head">
          <div className="agent-panel__title">
            <Activity size={15} />
            <span>{tr("Agent 活动")}</span>
            <span className="agent-panel__count">
              {events.length}{tr(" 条 · Agent ")}{agentCount}
            </span>
          </div>
          <div className="agent-panel__actions">
            <Button variant="ghost" size="sm" disabled={!state.currentId || archiving}
              onClick={() => void archive()} aria-label={tr("保存制作归档")}
              title={archiving ? tr("正在归档…") : tr("保存工程、历史与当前画面")}>
              <Archive size={14} className={archiving ? "agent-spin" : ""} /><span hidden>{tr("制作归档")}</span>
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void load()}
              disabled={loading}
              aria-label={tr("刷新Agent活动")}
              title={tr("刷新")}
            >
              <RefreshCw size={14} className={loading ? "agent-spin" : ""} />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={close}
              aria-label={tr("关闭Agent活动")}
              title={tr("关闭")}
            >
              <X size={15} />
            </Button>
          </div>
        </header>

        <div className="agent-panel__body">
          {archivePath ? <p className="agent-empty" role="status">{tr("制作归档已保存：{0}", archivePath)}</p> : null}
          {!state.currentId ? (
            <span className="agent-empty">{tr("请先选择一个工程")}</span>
          ) : error ? (
            <span className="agent-empty agent-empty--err">{error}</span>
          ) : loading && events.length === 0 ? (
            <span className="agent-loading">{tr("加载活动…")}</span>
          ) : events.length === 0 ? (
            <span className="agent-empty">{tr("暂无活动记录")}</span>
          ) : (
            <ul className="agent-event-list">
              {events.map((ev) => {
                const agent = isAgentActor(ev.actor);
                return (
                  <li key={ev.eventId} className="agent-event">
                    <div className="agent-event__head">
                      <span
                        className={`agent-event__actor agent-event__actor--${
                          agent ? "agent" : "human"
                        }`}
                      >
                        {agent ? <Bot size={12} /> : <User size={12} />}
                        {agent ? "Agent" : tr("人工")}
                      </span>
                      <span className="agent-event__rev cv-mono">R{Number(ev.revision)}</span>
                      <span className="agent-event__type cv-mono">{ev.type}</span>
                      {ev.actor?.id ? (
                        <span className="agent-event__id cv-mono" title="actor.id">
                          {ev.actor.id}
                        </span>
                      ) : null}
                    </div>

                    <ul className="agent-event__entities">
                      {ev.changedEntities.map((ce, i) => (
                        <li key={`${ce.type}-${ce.id}-${i}`}>
                          <button
                            type="button"
                            className="agent-entity"
                            onClick={() => handleNavigate(ce)}
                            aria-label={tr("跳转到{0}", entitySummary(ce))}
                            title={tr("点击定位到该对象")}
                          >
                            <ArrowRightLeft size={12} />
                            <span>{entitySummary(ce)}</span>
                          </button>
                        </li>
                      ))}
                    </ul>

                    <div className="agent-event__foot">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => void handleUndoTo(ev)}
                        disabled={busyRev === ev.eventId}
                        aria-label={tr("撤销到此_R{0}", Number(ev.revision))}
                        title={tr("连续撤销回到 revision {0}", ev.previousRevision)}
                      >
                        <Undo2 size={13} />{tr("撤销到此")}</Button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <footer className="agent-panel__foot">
          {lastSync ? (
            <span className="agent-panel__sync">{tr("更新于 ")}{new Date(lastSync).toLocaleTimeString(locale, { hour12: false })}
            </span>
          ) : (
            <span className="agent-panel__sync">{tr("尚未加载")}</span>
          )}
          <span className="agent-panel__hint">
            <Cpu size={11} />{tr(" 事件 outbox · 撤销走 history.undo")}</span>
        </footer>
      </aside>
    </>
  );
}
