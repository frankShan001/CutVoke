/** 片段特效小节：按素材适用类型筛选；调参一次原子更新。 */
import { useEffect, useMemo, useRef, useState } from "react";
import { SlidersHorizontal } from "lucide-react";
import { showError, useEditor } from "../../store/editor";
import { getLatestState } from "../../store/actions";
import { addEffect, removeEffect } from "../../store/clipEdit";
import { updateEffect } from "../../store/effectEdit";
import {
  effectsByCategory,
  getEffectCatalog,
  iconForCategory,
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
  const { state, dispatch } = useEditor();
  const [specs, setSpecs] = useState<EffectSpec[]>([]);
  const [busy, setBusy] = useState(false);
  const mediaKind = inferKind(clip.assetRef.sourcePath, false, false);
  const targetTypes = trackKind === "audio" ? ["audio"]
    : trackKind === "text" ? ["text"]
    : mediaKind === "image" ? ["image"]
    : ["video", "audio"];

  useEffect(() => {
    let cancelled = false;
    getEffectCatalog()
      .then((all) => {
        if (!cancelled) setSpecs(effectsByCategory(all, "fx"));
      })
      .catch((err) => {
        if (!cancelled) showError(dispatch, err);
      });
    return () => {
      cancelled = true;
    };
  }, [dispatch]);

  const isActive = (id: string) => clip.effects?.some((e) => e.effectId === id) ?? false;
  const applicableSpecs = specs.filter(
    (spec) => spec.appliesTo.some((type) => targetTypes.includes(type)) || isActive(spec.effectId),
  );
  const paramsOf = (id: string) =>
    (clip.effects?.find((e) => e.effectId === id)?.params as Record<string, unknown>) ?? {};

  const toggle = async (spec: EffectSpec) => {
    if (busy) return;
    setBusy(true);
    const ctx = getLatestState() || state;
    if (isActive(spec.effectId)) {
      await removeEffect(dispatch, ctx, { clipId: clip.id, effectId: spec.effectId });
    } else {
      await addEffect(dispatch, ctx, {
        clipId: clip.id,
        effectId: spec.effectId,
        params: { ...spec.defaults },
      });
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

  const Icon = iconForCategory("fx");
  const activeSpecs = applicableSpecs.filter((s) => isActive(s.effectId));
  const sectionLabel = trackKind === "audio" ? "音频特效" : mediaKind === "image" ? "画面特效" : "片段特效";

  return (
    <>
      <div className="inspector__sep" />
      <details className="inspector__adv inspector__fx-picker">
        <summary style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
          <span className="inspector__key">
            <SlidersHorizontal size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />
            {sectionLabel}
          </span>
          <span className="cv-hint">
            {applicableSpecs.length ? `${activeSpecs.length}/${applicableSpecs.length} 已启用` : "展开添加"}
          </span>
        </summary>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
          {applicableSpecs.map((s) => {
            const on = isActive(s.effectId);
            return (
              <button
                key={s.effectId}
                type="button"
                aria-label={s.name}
                aria-pressed={on}
                className={"cv-chip" + (on ? " cv-chip--on" : "")}
                title={s.description}
                onClick={() => void toggle(s)}
              >
                <Icon size={12} /> {s.name}
              </button>
            );
          })}
        </div>
      </details>
      {activeSpecs.map((s) => (
        <FxParams
          key={s.effectId}
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
  spec,
  params,
  onCommit,
}: {
  spec: EffectSpec;
  params: Record<string, unknown>;
  onCommit: (next: Record<string, unknown>) => Promise<boolean>;
}) {
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
    return true;
  }), [spec, local]);

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
    <>
      {isColorBalance ? (
        <>
          <ColorWheels params={local} onPreview={setLocal} onCommit={commitLocal} />
          <details className="inspector__adv color-wheels__numeric">
            <summary>RGB 数值微调</summary>
            {numericControls}
          </details>
        </>
      ) : numericControls}
      {isLut ? (
        <div className="lut-import">
          <input ref={lutInput} type="file" accept=".cube" hidden
            aria-label="导入 3D LUT 文件"
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
                else setImportError("LUT 已导入，但未能应用到片段；请重试");
              }).finally(() => setImporting(false));
            }} />
          <div className="lut-import__actions">
            <button type="button" className="cv-chip" disabled={importing}
              onClick={() => lutInput.current?.click()}>
              {importing ? "正在导入…" : "导入 3D .cube LUT"}
            </button>
            {local.file ? (
              <button type="button" className="cv-chip" disabled={importing}
                onClick={() => {
                  const next = { ...local, file: "", name: "" };
                  void onCommit(next).then((ok) => {
                    if (ok) setLocal(next);
                  });
                }}>恢复内置 LUT</button>
            ) : null}
          </div>
          {local.file ? <p className="cv-hint">当前 LUT：{String(local.name || local.file).split(/[\\/]/).pop()}</p> : null}
          {importError ? <p role="alert" className="cv-hint">{importError}</p> : null}
          <p className="cv-hint">导入文件按 Rec.709/sRGB 画面解释；预览与导出共用同一 LUT 渲染链。</p>
        </div>
      ) : null}
    </>
  );
}
