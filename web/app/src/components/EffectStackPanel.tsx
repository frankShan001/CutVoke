/** 效果条面板（J01）：当前选中片段的完整效果栈（含转场）+ 个人预设管理。
 *
 * 顺序即渲染合成顺序（靠前先应用）。每行：中文名（注册表查，查不到显示 effectId）、
 * 关键参数摘要、上移/下移（effect.reorder toIndex）、旁路开关（effect.bypass，关闭时整行
 * 视觉降级但仍保留在栈中）、删除（effect.remove）。顶部可把当前栈存为个人预设
 * （preset.save {name, clipId}），下方列出该工程预设（preset.apply merge/replace + delete）。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Bookmark, Loader2, Save, Copy } from "lucide-react";
import { Panel, Button, TextInput } from "./ui";
import { useEditor, showError } from "../store/editor";
import { getLatestState } from "../store/actions";
import {
  bypassEffect,
  deletePreset,
  applyPreset,
  reorderEffect,
  removeEffectFromClip,
  savePreset,
} from "../store/effectEdit";
import { getEffectCatalog, listPresets, type EffectSpec, type Preset } from "../lib/effects";
import { EffectRow } from "./effectStack/EffectRow";
import { PresetList } from "./effectStack/PresetList";
import type { EffectInstance } from "../types/api";

interface Props {
  active: boolean;
}

export function EffectStackPanel({ active }: Props) {
  const { state, dispatch } = useEditor();
  const pid = state.currentId;
  const selectedClipId = state.selection?.clipId ?? null;

  const [catalog, setCatalog] = useState<EffectSpec[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [presetName, setPresetName] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getEffectCatalog()
      .then((all) => {
        if (!cancelled) setCatalog(all);
      })
      .catch((err) => {
        if (!cancelled) showError(dispatch, err);
      });
    return () => {
      cancelled = true;
    };
  }, [dispatch]);

  const loadPresets = useCallback(async () => {
    if (!pid) {
      setPresets([]);
      return;
    }
    try {
      setPresets(await listPresets(pid));
    } catch (err) {
      setPresets([]);
      showError(dispatch, err);
    }
  }, [pid, dispatch]);

  useEffect(() => {
    if (active && pid) void loadPresets();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, pid, state.revision, state.project?.revision]);

  const clipEffects = useMemo<EffectInstance[]>(() => {
    if (!selectedClipId) return [];
    const tracks = state.project?.sequence.tracks || [];
    for (const t of tracks) {
      const c = t.clips.find((x) => x.id === selectedClipId);
      if (c && Array.isArray(c.effects)) return c.effects;
    }
    return [];
  }, [selectedClipId, state.project]);

  const anyBusy = !!busyId;
  const run = async (fn: () => Promise<{ ok: boolean }>, busy: string | null) => {
    setBusyId(busy);
    const res = await fn();
    setBusyId(null);
    return res;
  };

  const handleSavePreset = async () => {
    const name = presetName.trim();
    if (!name) {
      showError(dispatch, { status: 400, code: "INVALID_ARGUMENT", message: "请先输入预设名称" });
      return;
    }
    if (!selectedClipId) return;
    if (clipEffects.length === 0) {
      showError(dispatch, {
        status: 400,
        code: "INVALID_ARGUMENT",
        message: "当前片段没有可保存的效果",
      });
      return;
    }
    const st = getLatestState() || state;
    const res = await run(() => savePreset(dispatch, st, { name, clipId: selectedClipId }), "save");
    if (res.ok) {
      setPresetName("");
      void loadPresets();
    }
  };

  return (
    <Panel title="效果条" subtitle="顺序即渲染合成顺序">
      {!selectedClipId ? (
        <p className="cv-empty">先在时间线选中一个片段，这里会列出它的完整效果栈。</p>
      ) : clipEffects.length === 0 ? (
        <p className="cv-empty">该片段还没有效果。到「资源库」选择一个应用到选中片段。</p>
      ) : (
        <div className="effect-stack">
          {clipEffects.map((e, i) => (
            <EffectRow
              key={`${i}:${String(e.effectId)}`}
              effect={e}
              index={i}
              total={clipEffects.length}
              catalog={catalog}
              busy={anyBusy}
              onMove={(dir) => {
                const st = getLatestState() || state;
                void run(
                  () =>
                    reorderEffect(dispatch, st, {
                      clipId: String(selectedClipId),
                      effectId: String(e.effectId),
                      toIndex: i + dir,
                    }),
                  `mv:${i}:${dir}`,
                );
              }}
              onBypass={() => {
                const st = getLatestState() || state;
                void run(
                  () =>
                    bypassEffect(dispatch, st, {
                      clipId: String(selectedClipId),
                      effectId: String(e.effectId),
                      enabled: e.enabled !== false ? false : true,
                    }),
                  `bp:${i}`,
                );
              }}
              onRemove={() => {
                const st = getLatestState() || state;
                void run(
                  () =>
                    removeEffectFromClip(dispatch, st, {
                      clipId: String(selectedClipId),
                      effectId: String(e.effectId),
                    }),
                  `rm:${i}`,
                );
              }}
            />
          ))}
        </div>
      )}

      <div className="inspector__sep" />

      <div className="inspector__subrow" style={{ gap: 6, alignItems: "center" }}>
        <span className="inspector__key">
          <Save size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />
          存为预设
        </span>
      </div>
      <div className="effect-preset-save">
        <TextInput
          value={presetName}
          onChange={(e) => setPresetName(e.target.value)}
          placeholder="预设名称"
          aria-label="预设名称"
          onKeyDown={(e) => {
            if (e.key === "Enter") void handleSavePreset();
          }}
        />
        <Button
          variant="secondary"
          size="sm"
          disabled={!selectedClipId || clipEffects.length === 0 || busyId === "save"}
          onClick={() => void handleSavePreset()}
          aria-label="保存当前效果栈为预设"
        >
          {busyId === "save" ? <Loader2 size={12} className="cv-spin" /> : <Bookmark size={12} />}
          保存
        </Button>
      </div>
      <p className="cv-hint" style={{ marginTop: 4 }}>
        保存后可在任意片段上复用这组效果（含参数）。
      </p>

      <div className="inspector__subrow" style={{ gap: 6, marginTop: 6, alignItems: "center" }}>
        <span className="inspector__key">
          <Copy size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />
          个人预设
        </span>
        <span className="cv-mono" style={{ fontSize: 10.5, color: "var(--text-faint)" }}>
          {presets.length} 个
        </span>
      </div>
      <PresetList
        presets={presets}
        hasClip={!!selectedClipId}
        busyId={busyId}
        onApply={(p, mode) => {
          if (!selectedClipId) return;
          const st = getLatestState() || state;
          void run(
            () => applyPreset(dispatch, st, { clipId: selectedClipId, presetId: p.id, mode }),
            `ap:${p.id}:${mode}`,
          );
        }}
        onDelete={(p) => {
          const st = getLatestState() || state;
          void run(() => deletePreset(dispatch, st, { presetId: p.id }), `dl:${p.id}`).then((res) => {
            if (res.ok) void loadPresets();
          });
        }}
      />

      <p className="cv-hint" style={{ marginTop: 8 }}>
        效果栈从上到下依次叠加；旁路的效果仍保留但暂不参与渲染。
      </p>
    </Panel>
  );
}