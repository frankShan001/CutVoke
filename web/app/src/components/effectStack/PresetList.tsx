/** 个人预设列表（J01）：应用（合并/替换）+ 删除。 */
import { Loader2, Trash2 } from "lucide-react";
import type { Preset } from "../../lib/effects";

export function PresetList({
  presets,
  hasClip,
  busyId,
  onApply,
  onDelete,
}: {
  presets: Preset[];
  hasClip: boolean;
  busyId: string | null;
  onApply: (preset: Preset, mode: "merge" | "replace") => void;
  onDelete: (preset: Preset) => void;
}) {
  if (presets.length === 0) {
    return <p className="cv-empty cv-empty--tight">暂无预设。</p>;
  }
  return (
    <div className="effect-presets">
      {presets.map((p) => (
        <div key={p.id} className="effect-preset">
          <div className="effect-preset__name" title={p.id}>
            {p.name}
            <span className="effect-preset__count">{p.effects.length} 个效果</span>
          </div>
          <div className="effect-preset__actions">
            <button
              type="button"
              className="cv-btn cv-btn--sm cv-btn--secondary"
              disabled={!hasClip || busyId === `ap:${p.id}:merge`}
              aria-label={`合并应用预设 ${p.name}`}
              title="应用（合并）：保留原有效果并追加"
              onClick={() => onApply(p, "merge")}
            >
              应用·合并
            </button>
            <button
              type="button"
              className="cv-btn cv-btn--sm cv-btn--secondary"
              disabled={!hasClip || busyId === `ap:${p.id}:replace`}
              aria-label={`替换应用预设 ${p.name}`}
              title="应用（替换）：先移除同类别效果再追加"
              onClick={() => onApply(p, "replace")}
            >
              应用·替换
            </button>
            <button
              type="button"
              className="cv-btn cv-btn--sm cv-btn--danger"
              disabled={busyId === `dl:${p.id}`}
              aria-label={`删除预设 ${p.name}`}
              title="删除预设"
              onClick={() => onDelete(p)}
            >
              {busyId === `dl:${p.id}` ? <Loader2 size={12} className="cv-spin" /> : <Trash2 size={12} />}
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}