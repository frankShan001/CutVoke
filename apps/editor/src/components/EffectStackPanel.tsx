import { tr, useLocale } from "../i18n";
/** 效果条面板（J01）：当前选中片段的完整效果栈（含转场）+ 个人预设管理。
 *
 * 顺序即渲染合成顺序（靠前先应用）。每行：中文名（注册表查，查不到显示 effectId）、
 * 关键参数摘要、上移/下移（effect.reorder toIndex）、旁路开关（effect.bypass，关闭时整行
 * 视觉降级但仍保留在栈中）、删除（effect.remove）。顶部可把当前栈存为个人预设
 * （preset.save {name, clipId}），下方列出该工程预设（preset.apply merge/replace + delete）。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Bookmark, Loader2, Save, Copy } from "lucide-react";
import { Panel, Button, Select, TextInput } from "./ui";
import { useEditor, showError } from "../store/editor";
import { getLatestState } from "../store/actions";
import {
  bypassEffect,
  copyVisualEffects,
  deletePreset,
  applyPreset,
  reorderEffect,
  removeEffectFromClip,
  savePreset,
  updateEffect,
} from "../store/effectEdit";
import { getEffectCatalog, listPresets, type EffectSpec, type Preset } from "../lib/effects";
import { EffectRow } from "./effectStack/EffectRow";
import { PresetList } from "./effectStack/PresetList";
import { PersonCutoutSection } from "./effectStack/PersonCutoutSection";
import type { EffectInstance } from "../types/api";

interface Props {
  active: boolean;
}

export function EffectStackPanel({ active }: Props) {
  const locale = useLocale();
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const pid = state.currentId;
  const selectedClipId = state.selection?.clipId ?? null;
  const selectedTrackLocked = !!state.project?.sequence.tracks.find((track) =>
    track.clips.some((clip) => clip.id === selectedClipId),
  )?.locked;
  const readOnly = selectedTrackLocked || !!state.editLock;
  const copyReadOnly = !!state.editLock;

  const [catalog, setCatalog] = useState<EffectSpec[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [presetName, setPresetName] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [copyTargets, setCopyTargets] = useState<string[]>([]);
  const [copyMode, setCopyMode] = useState<"replace" | "merge">("replace");
  const [search,setSearch] = useState(''), [modifiedOnly,setModifiedOnly] = useState(false);

  useEffect(() => setCopyTargets([]), [pid, selectedClipId]);

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
  }, [dispatch, locale]);

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
  }, [selectedClipId, state.project, locale]);

  const visualEffectIds = useMemo(() => new Set(
    catalog.filter((spec) => ["fx", "filter", "color"].includes(spec.browseCategory))
      .map((spec) => spec.effectId),
  ), [catalog, locale]);
  const visibleEffects = clipEffects.map((effect,index)=>({effect,index})).filter(({effect,index})=>{
    const spec=catalog.find(item=>item.effectId===effect.effectId);
    const words=`${spec?.name || ''} ${effect.effectId}`.toLocaleLowerCase();
    if(!search.toLocaleLowerCase().trim().split(/\s+/).every(token=>words.includes(token)))return false;
    if(!modifiedOnly)return true;
    const prefix=`effect:${effect.instanceId || `legacy_${index}`}:`;
    const clip=state.project?.sequence.tracks.flatMap(track=>track.clips).find(clip=>clip.id===selectedClipId);
    return effect.enabled===false || !!effect.range || !!effect.compositing || Object.entries(effect.params || {}).some(([key,value])=>
      JSON.stringify(value)!==JSON.stringify(spec?.defaults[key])) ||
      Object.entries(clip?.keyframes || {}).some(([key,lane])=>key.startsWith(prefix) && lane.length>0);
  });
  const visualEffectCount = clipEffects.filter((effect) => visualEffectIds.has(effect.effectId)).length;
  const targetClips = useMemo(() => (
    (state.project?.sequence.tracks ?? [])
      .filter((track) => track.kind === "video" && track.role !== "sticker")
      .flatMap((track) => track.clips.filter((clip) => clip.role !== "sticker").map((clip, index) => ({
        id: clip.id,
        locked: !!track.locked,
        label: tr("{0} · 第 {1} 段 · {2}", track.id, index + 1, clip.assetRef.sourcePath.split(/[\\/]/).pop() || clip.id),
      })))
      .filter((item) => item.id !== selectedClipId)
  ), [state.project, selectedClipId, locale]);
  const availableTargetIds = targetClips.filter((item) => !item.locked).map((item) => item.id);

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
      showError(dispatch, { status: 400, code: "INVALID_ARGUMENT", message: tr("请先输入预设名称") });
      return;
    }
    if (!selectedClipId) return;
    if (clipEffects.length === 0) {
      showError(dispatch, {
        status: 400,
        code: "INVALID_ARGUMENT",
        message: tr("当前片段没有可保存的效果"),
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
    <Panel title={tr("效果条")} subtitle={tr("顺序即渲染合成顺序")}>
      {selectedClipId && readOnly ? (
        <p className="cv-hint" role="status">
          {state.editLock ? tr("Agent 正在编辑，效果栈暂时只读。") : tr("所选片段所在轨道已锁定，解锁后可调整效果。")}
        </p>
      ) : null}
      {!selectedClipId ? (
        <p className="cv-empty">{tr("先在时间线选中一个片段，这里会列出它的完整效果栈。")}</p>
      ) : clipEffects.length === 0 ? (
        <p className="cv-empty">{tr("该片段还没有效果。到左侧「特效」或「滤镜」选择并应用到当前片段。")}</p>
      ) : (
        <div className="effect-stack">
          <input className="cv-input" aria-label={tr('搜索当前效果栈')} placeholder={tr('搜索当前效果栈')} value={search} onChange={event=>setSearch(event.target.value)}/>
          <label className="cv-hint"><input type="checkbox" checked={modifiedOnly} onChange={event=>setModifiedOnly(event.target.checked)}/>{tr('仅显示已修改')}</label>
          {!visibleEffects.length ? <p className="cv-empty" role="status">{tr('没有匹配结果')}</p> : null}
          {visibleEffects.map(({effect:e,index:i}) => (
            <EffectRow
              key={e.instanceId || `legacy_${i}`}
              effect={e}
              index={i}
              total={clipEffects.length}
              catalog={catalog}
              busy={anyBusy || readOnly}
              onEdit={()=>window.dispatchEvent(new CustomEvent('cutvoke:edit-effect',{detail:{effectId:e.effectId,
                category:catalog.find(item=>item.effectId===e.effectId)?.category || 'fx',clipId:selectedClipId,
                instanceId:e.instanceId || `legacy_${i}`}}))}
              onReset={()=>{if(readOnly)return;void run(()=>updateEffect(dispatch,getLatestState() || state,{
                clipId:String(selectedClipId),effectId:e.effectId,instanceId:e.instanceId || `legacy_${i}`,params:{},replaceParams:true}),`reset:${i}`);}}
              onMove={(dir) => {
                if (readOnly) return;
                const st = getLatestState() || state;
                void run(
                  () =>
                    reorderEffect(dispatch, st, {
                      clipId: String(selectedClipId),
                      effectId: String(e.effectId),
                      instanceId: e.instanceId || `legacy_${i}`,
                      toIndex: i + dir,
                    }),
                  `mv:${i}:${dir}`,
                );
              }}
              onBypass={() => {
                if (readOnly) return;
                const st = getLatestState() || state;
                void run(
                  () =>
                    bypassEffect(dispatch, st, {
                      clipId: String(selectedClipId),
                      effectId: String(e.effectId),
                      instanceId: e.instanceId || `legacy_${i}`,
                      enabled: e.enabled !== false ? false : true,
                    }),
                  `bp:${i}`,
                );
              }}
              onRemove={() => {
                if (readOnly) return;
                const st = getLatestState() || state;
                void run(
                  () =>
                    removeEffectFromClip(dispatch, st, {
                      clipId: String(selectedClipId),
                      effectId: String(e.effectId),
                      instanceId: e.instanceId || `legacy_${i}`,
                    }),
                  `rm:${i}`,
                );
              }}
            />
          ))}
        </div>
      )}

      <PersonCutoutSection
        active={active}
        projectId={pid}
        clipId={selectedClipId}
        readOnly={readOnly}
      />

      <details className="effect-copy">
        <summary>{tr("复制画面效果、滤镜与调色")}</summary>
        <p className="cv-hint">{tr("从当前片段复制 ")}{visualEffectCount}{tr(" 项画面效果及微调参数到目标片段。一次操作可以整体撤销。")}</p>
        {targetClips.length ? (
          <>
            <div className="effect-copy__toolbar">
              <Button variant="secondary" size="sm" disabled={copyReadOnly || anyBusy || !availableTargetIds.length}
                onClick={() => setCopyTargets(availableTargetIds)}>{tr("全选可编辑片段")}</Button>
              <Button variant="secondary" size="sm" disabled={anyBusy || !copyTargets.length}
                onClick={() => setCopyTargets([])}>{tr("清空")}</Button>
            </div>
            <div className="effect-copy__targets" role="group" aria-label={tr("复制画面效果的目标片段")}>
              {targetClips.map((target) => (
                <label key={target.id} className="effect-copy__target" title={target.label}>
                  <input type="checkbox" checked={copyTargets.includes(target.id)}
                    disabled={copyReadOnly || anyBusy || target.locked}
                    onChange={(event) => setCopyTargets((current) => event.target.checked
                      ? [...current, target.id] : current.filter((id) => id !== target.id))} />
                  <span>{target.label}{target.locked ? tr("（轨道已锁定）") : ""}</span>
                </label>
              ))}
            </div>
            <div className="effect-copy__toolbar">
              <Select aria-label={tr("复制方式")} value={copyMode}
                onChange={(event) => setCopyMode(event.target.value as "replace" | "merge")}
                disabled={copyReadOnly || anyBusy}>
                <option value="replace">{tr("替换目标画面效果")}</option>
                <option value="merge">{tr("保留其它画面效果")}</option>
              </Select>
              <Button variant="secondary" size="sm"
                disabled={!selectedClipId || copyReadOnly || anyBusy || !visualEffectCount ||
                  !copyTargets.some((id) => availableTargetIds.includes(id))}
                onClick={() => {
                  if (!selectedClipId) return;
                  const ids = copyTargets.filter((id) => availableTargetIds.includes(id));
                  const st = getLatestState() || state;
                  void run(() => copyVisualEffects(dispatch, st, {
                    sourceClipId: selectedClipId, targetClipIds: ids, mode: copyMode,
                  }), "copyVisual").then((result) => {
                    if (result.ok) setCopyTargets([]);
                  });
                }}>
                {busyId === "copyVisual" ? <Loader2 size={12} className="cv-spin" /> : <Copy size={12} />}{tr("复制到 ")}{copyTargets.filter((id) => availableTargetIds.includes(id)).length}{tr(" 段")}</Button>
            </div>
          </>
        ) : <p className="cv-hint">{tr("工程中还没有其它视频或图片片段。")}</p>}
      </details>

      <div className="inspector__sep" />

      <div className="inspector__subrow" style={{ gap: 6, alignItems: "center" }}>
        <span className="inspector__key">
          <Save size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />{tr("存为预设")}</span>
      </div>
      <div className="effect-preset-save">
        <TextInput
          value={presetName}
          onChange={(e) => setPresetName(e.target.value)}
          placeholder={tr("预设名称")}
          aria-label={tr("预设名称")}
          onKeyDown={(e) => {
            if (e.key === "Enter") void handleSavePreset();
          }}
        />
        <Button
          variant="secondary"
          size="sm"
          disabled={!selectedClipId || clipEffects.length === 0 || busyId === "save"}
          onClick={() => void handleSavePreset()}
          aria-label={tr("保存当前效果栈为预设")}
        >
          {busyId === "save" ? <Loader2 size={12} className="cv-spin" /> : <Bookmark size={12} />}{tr("保存")}</Button>
      </div>
      <p className="cv-hint" style={{ marginTop: 4 }}>{tr("保存后可在任意片段上复用这组效果（含参数）。")}</p>

      <div className="inspector__subrow" style={{ gap: 6, marginTop: 6, alignItems: "center" }}>
        <span className="inspector__key">
          <Copy size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />{tr("个人预设")}</span>
        <span className="cv-mono" style={{ fontSize: 10.5, color: "var(--text-faint)" }}>
          {presets.length}{tr(" 个")}</span>
      </div>
      <PresetList
        presets={presets}
        hasClip={!!selectedClipId && !readOnly}
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

      <p className="cv-hint" style={{ marginTop: 8 }}>{tr("效果栈从上到下依次叠加；旁路的效果仍保留但暂不参与渲染。")}</p>
    </Panel>
  );
}
