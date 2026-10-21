import { tr, useLocale } from "../../i18n";
/** 片段特效小节：按素材适用类型筛选；调参一次原子更新。 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Plus, SlidersHorizontal } from "lucide-react";
import { showError, useEditor } from "../../store/editor";
import { getLatestState } from "../../store/actions";
import { removeEffect } from "../../store/clipEdit";
import { bypassEffect, updateEffect } from "../../store/effectEdit";
import {
  effectsByCategory,
  getEffectCatalog,
  type EffectSpec,
} from "../../lib/effects";
import { paramControls } from "../../lib/effectControls";
import { ParamControls } from "./ParamControls";
import { inferKind } from "../../lib/assetStore";
import { uploadLut } from "../../lib/lutApi";
import { ColorWheels } from "./ColorWheels";
import type { Clip } from "../../types/api";

function preserveMaskBoundsOnShapeChange(
  previous: Record<string, unknown>, next: Record<string, unknown>,
): Record<string, unknown> {
  const from = previous.shape === "circle";
  const to = next.shape === "circle";
  if (from === to) return next;
  const w = Number(previous.w ?? 0.7);
  const h = Number(previous.h ?? 0.7);
  const x = Number(previous.x ?? 0.15);
  const y = Number(previous.y ?? 0.15);
  const clamp = (value: number, min: number, max: number) =>
    Math.round(Math.min(Math.max(min, max), Math.max(min, value)) * 10000) / 10000;
  return to
    ? { ...next, x: clamp(x + w / 2, w / 2, 1 - w / 2),
        y: clamp(y + h / 2, h / 2, 1 - h / 2) }
    : { ...next, x: clamp(x - w / 2, 0, 1 - w),
        y: clamp(y - h / 2, 0, 1 - h) };
}

export function FxSection({ clip, trackKind }: { clip: Clip; trackKind: string }) {
  const locale = useLocale();
  const { state, dispatch } = useEditor();
  const [specs, setSpecs] = useState<EffectSpec[]>([]);
  const [busy, setBusy] = useState(false);
  const mediaKind = inferKind(clip.assetRef.sourcePath, false, false);

  useEffect(() => {
    let cancelled = false;
    getEffectCatalog()
      .then((all) => {
        if (!cancelled) setSpecs([...effectsByCategory(all, "fx"), ...effectsByCategory(all, "color")]);
      })
      .catch((err) => {
        if (!cancelled) showError(dispatch, err);
      });
    return () => {
      cancelled = true;
    };
  }, [dispatch, locale]);

  const isActive = (id: string) => clip.effects?.some((e) => e.effectId === id) ?? false;
  const paramsOf = (id: string) =>
    (clip.effects?.find((e) => e.effectId === id)?.params as Record<string, unknown>) ?? {};

  const toggle = async (spec: EffectSpec) => {
    if (busy) return;
    setBusy(true);
    const ctx = getLatestState() || state;
    if (isActive(spec.effectId)) {
      await removeEffect(dispatch, ctx, { clipId: clip.id, effectId: spec.effectId });
    }
    setBusy(false);
  };

  const setParams = async (spec: EffectSpec, params: Record<string, unknown>): Promise<boolean> => {
    if (busy) return false;
    setBusy(true);
    const ctx = getLatestState() || state;
    const result = await updateEffect(dispatch, ctx, {
      clipId: clip.id, effectId: spec.effectId, params,
    });
    setBusy(false);
    return result.ok;
  };

  const activeSpecs = specs.filter((s) => isActive(s.effectId));
  const sectionLabel = trackKind === "audio" ? tr("音频特效") : mediaKind === "image" ? tr("画面特效") : tr("片段特效");

  return (
    <>
      <div className="inspector__sep" />
      <div className="inspector__effects-heading">
        <span className="inspector__key"><SlidersHorizontal size={13} /> {sectionLabel}</span>
        <button type="button" className="cv-chip inspector__add-effect"
          onClick={() => window.dispatchEvent(new CustomEvent("cutvoke:open-creative-domain", { detail: "effects" }))}>
          <Plus size={12} />{tr("从效果库添加")}
        </button>
      </div>
      {activeSpecs.map((s, index) => (
        <FxParams
          key={`${clip.id}:${s.effectId}`}
          clipId={clip.id}
          initiallyOpen={index === 0}
          enabled={clip.effects?.find((effect) => effect.effectId === s.effectId)?.enabled !== false}
          onRemove={() => void toggle(s)}
          busy={busy}
          onEnabled={async (enabled) => {
            if (busy) return false;
            setBusy(true);
            try { return (await bypassEffect(dispatch, getLatestState() || state, { clipId: clip.id, effectId: s.effectId, enabled })).ok; }
            finally { setBusy(false); }
          }}
          spec={s}
          params={paramsOf(s.effectId)}
          onCommit={(p) => setParams(s, p)}
        />
      ))}
    </>
  );
}

/** 单个已生效滤镜的参数编辑器（本地态，提交时才发命令）。 */
function FxParams({
  clipId, initiallyOpen, enabled, busy, onRemove, onEnabled,
  spec,
  params,
  onCommit,
}: {
  clipId: string;
  initiallyOpen: boolean;
  enabled: boolean;
  busy: boolean;
  onRemove(): void;
  onEnabled(enabled: boolean): Promise<boolean>;
  spec: EffectSpec;
  params: Record<string, unknown>;
  onCommit: (next: Record<string, unknown>) => Promise<boolean>;
}) {
  const locale = useLocale();
  const [expanded, setExpanded] = useState(initiallyOpen);
  const [locallyEnabled, setLocallyEnabled] = useState(enabled);
  useEffect(() => setLocallyEnabled(enabled), [enabled]);
  useEffect(() => {
    const open = (event: Event) => {
      const target = (event as CustomEvent<{ effectId?: string; clipId?: string }>).detail;
      if (target?.effectId === spec.effectId && (!target.clipId || target.clipId === clipId)) setExpanded(true);
    };
    window.addEventListener("cutvoke:edit-effect", open);
    return () => window.removeEventListener("cutvoke:edit-effect", open);
  }, [clipId, spec.effectId]);
  const [local, setLocal] = useState<Record<string, unknown>>(params);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState("");
  const lutInput = useRef<HTMLInputElement>(null);
  const paramKey = JSON.stringify(params);
  useEffect(() => {
    setLocal(params);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spec.effectId, paramKey]);

  const isLut = spec.effectId === "cutvoke.fx.lut";
  const isColorBalance = spec.effectId === "cutvoke.fx.colorbalance";
  const controls = useMemo(() => paramControls(spec, local).filter((control) => {
    if (spec.effectId === "cutvoke.fx.lut" &&
        (control.name === "file" || control.name === "name")) return false;
    if (spec.effectId === "cutvoke.fx.mask" && local.shape !== "text" &&
        (control.name === "content" || control.name === "fontSize")) return false;
    if (spec.effectId === "cutvoke.fx.mask") {
      if (control.name.startsWith('tile') && local.shape !== 'text') return false;
      if (control.name.startsWith('ink') && local.shape !== 'ink') return false;
      if (['fontFamily','bold'].includes(control.name) && local.shape !== 'text') return false;
      if (['tileSpacing','tileDrift'].includes(control.name) && !local.tileText) return false;
      if (control.name === "angle" && local.shape !== "wipe") return false;
      if (control.name === "pathMode" && local.shape !== "freehand") return false;
      if (control.name === "aspectMode" && local.shape !== "circle") return false;
      if (local.shape === "wipe" && ["w", "h"].includes(control.name)) return false;
      if (control.name.startsWith("motion") && control.name !== "motion" &&
          (!local.motion || local.motion === "none")) return false;
      if (control.name === "motionRamp" && local.motion !== "inertia") return false;
    }
    return true;
  }), [spec, local, locale]);

  const commitLocal = (next: Record<string, unknown>) => {
    if (JSON.stringify(next) === JSON.stringify(params)) return;
    void onCommit(next).then((ok) => {
      if (!ok) setLocal(params);
    });
  };

  const numericControls = (
    <ParamControls
      controls={controls}
      onChange={(name, value) => {
        const next = { ...local };
        if (value === undefined) delete next[name];
        else next[name] = value;
        setLocal(next);
      }}
      onCommit={(change) => {
        let next = { ...local };
        if (change) {
          if (change.value === undefined) delete next[change.name];
          else next[change.name] = change.value;
          if (spec.effectId === "cutvoke.fx.mask" && change.name === "shape") {
            next = preserveMaskBoundsOnShapeChange(params, next);
          }
          if (isLut && change.name === "preset") {
            next.file = "";
            next.name = "";
          }
        }
        commitLocal(next);
      }}
    />
  );

  return (
    <details className={`inspector__effect-params${enabled ? "" : " inspector__effect-params--disabled"}`} data-effect-id={spec.effectId}
      aria-label={tr("{0}参数", spec.name)} open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary>{spec.name}</summary>
      <div className="inspector__effect-actions">
        <label><input type="checkbox" checked={locallyEnabled} disabled={busy} onChange={(event) => {
          const next = event.currentTarget.checked; setLocallyEnabled(next);
          void onEnabled(next).then(ok => { if (!ok) setLocallyEnabled(enabled); });
        }}
          aria-label={tr("启用效果 {0}", spec.name)} />{tr("启用")}</label>
        <button type="button" className="cv-chip" disabled={busy} onClick={onRemove} aria-label={tr("删除效果 {0}", spec.name)}>{tr("删除效果")}</button>
      </div>
      {isColorBalance ? (
        <>
          <ColorWheels params={local} onPreview={setLocal} onCommit={commitLocal} />
          <details className="inspector__adv color-wheels__numeric">
            <summary>{tr("RGB 数值微调")}</summary>
            {numericControls}
          </details>
        </>
      ) : numericControls}
      {isLut ? (
        <div className="lut-import">
          <input ref={lutInput} type="file" accept=".cube" hidden
            aria-label={tr("导入 3D LUT 文件")}
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (!file) return;
              setImporting(true);
              setImportError("");
              void uploadLut(file).then(async (result) => {
                if (result.kind === "error") {
                  setImportError(result.message);
                  return;
                }
                const next = { ...local, file: result.data.path, name: result.data.name };
                if (await onCommit(next)) setLocal(next);
                else setImportError(tr("LUT 已导入，但未能应用到片段；请重试"));
              }).finally(() => setImporting(false));
            }} />
          <div className="lut-import__actions">
            <button type="button" className="cv-chip" disabled={importing}
              onClick={() => lutInput.current?.click()}>
              {importing ? tr("正在导入…") : tr("导入 3D .cube LUT")}
            </button>
            {local.file ? (
              <button type="button" className="cv-chip" disabled={importing}
                onClick={() => {
                  const next = { ...local, file: "", name: "" };
                  void onCommit(next).then((ok) => {
                    if (ok) setLocal(next);
                  });
                }}>{tr("恢复内置 LUT")}</button>
            ) : null}
          </div>
          {local.file ? <p className="cv-hint">{tr("当前 LUT：")}{String(local.name || local.file).split(/[\\/]/).pop()}</p> : null}
          {importError ? <p role="alert" className="cv-hint">{importError}</p> : null}
          <p className="cv-hint">{tr("导入文件按 Rec.709/sRGB 画面解释；预览与导出共用同一 LUT 渲染链。")}</p>
        </div>
      ) : null}
    </details>
  );
}
