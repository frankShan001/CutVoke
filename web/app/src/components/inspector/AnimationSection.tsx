/** 动画小节（J01 数据驱动）：选项与参数全部来自注册表 category=animation。
 *  语义沿用：后端 _find_animation 只取 clip.effects 第一个 cutvoke.anim.*，
 *  故做成单一选择器（同一时刻仅一个动画生效）；切换/调参走 applyAnimation
 *  （先清旧动画再 add，幂等，避免叠加）。 */
import { useEffect, useMemo, useState } from "react";
import { Sparkles } from "lucide-react";
import { showError, useEditor } from "../../store/editor";
import { getLatestState } from "../../store/actions";
import { applyAnimation } from "../../store/clipEdit";
import {
  effectsByCategory,
  getEffectCatalog,
  type EffectSpec,
} from "../../lib/effects";
import { paramControls } from "../../lib/effectControls";
import { ParamControls } from "./ParamControls";
import type { Clip } from "../../types/api";

const ANIM_PREFIX = "cutvoke.anim.";

export function AnimationSection({ clip }: { clip: Clip }) {
  const { state, dispatch } = useEditor();
  const [specs, setSpecs] = useState<EffectSpec[]>([]);
  const [animId, setAnimId] = useState("");
  const [params, setParams] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getEffectCatalog()
      .then((all) => {
        if (!cancelled) setSpecs(effectsByCategory(all, "animation"));
      })
      .catch((err) => {
        if (!cancelled) showError(dispatch, err);
      });
    return () => {
      cancelled = true;
    };
  }, [dispatch]);

  // 回显片段上已有动画
  useEffect(() => {
    const existing = clip.effects?.find((e) => String(e.effectId).startsWith(ANIM_PREFIX));
    setAnimId(existing ? String(existing.effectId) : "");
    setParams(existing && existing.params ? { ...existing.params } : {});
    setBusy(false);
  }, [clip.id, clip.effects]);

  const spec = useMemo(() => specs.find((s) => s.effectId === animId), [specs, animId]);
  const controls = useMemo(() => (spec ? paramControls(spec, params) : []), [spec, params]);

  const apply = async (nextId: string, nextParams: Record<string, unknown>) => {
    if (busy) return;
    setBusy(true);
    const ctx = getLatestState() || state;
    const res = await applyAnimation(dispatch, ctx, {
      clipId: clip.id,
      animationId: nextId,
      duration: 1, // 占位：params 已显式给出，不再走旧的按 id 推断分支
      params: nextParams,
    });
    if (res.ok) {
      setAnimId(nextId);
      setParams(nextParams);
    }
    setBusy(false);
  };

  const pick = (nextId: string) => {
    const nextSpec = specs.find((s) => s.effectId === nextId);
    void apply(nextId, nextSpec ? { ...nextSpec.defaults } : {});
  };

  const changeParam = (name: string, value: unknown) => {
    const next = { ...params };
    if (value === undefined) delete next[name];
    else next[name] = value;
    setParams(next);
  };

  return (
    <>
      <div className="inspector__sep" />
      <div className="inspector__subrow">
        <span className="inspector__key">
          <Sparkles size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />
          动画
        </span>
      </div>
      <select
        value={animId}
        aria-label="动画"
        className="cv-input"
        style={{ height: 28, width: "100%" }}
        disabled={busy}
        onChange={(e) => pick(e.target.value)}
      >
        <option value="">无</option>
        {specs.map((s) => (
          <option key={s.effectId} value={s.effectId}>
            {s.name}
          </option>
        ))}
      </select>
      {animId !== "" ? (
        <ParamControls
          controls={controls}
          onChange={changeParam}
          onCommit={() => void apply(animId, params)}
        />
      ) : null}
      <p className="cv-hint" style={{ marginTop: 4 }}>
        同一时刻只有一个动画生效（切换会自动替换上一个）。
      </p>
    </>
  );
}
