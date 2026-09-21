/** 资源库面板（J01）：分类 tab / 搜索 / 收藏/最近筛选 / 效果卡片 / 应用到选中片段。
 *
 * 数据全部来自 GET /projects/{id}/resources（工程级：带 favorite/recent 标记），
 * 分类由 category 枚举派生（绝对不写死效果 ID 列表）。应用效果走 effect.add +
 * 既有刷新机制；请求失败显式 showError，不静默降级。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Search, Star, Loader2, CheckSquare2, Layers } from "lucide-react";
import { Panel } from "./ui";
import { useEditor, showError } from "../store/editor";
import { getLatestState } from "../store/actions";
import { addEffectToClip, setFavorite } from "../store/effectEdit";
import {
  categoryLabelOf,
  iconForCategory,
  listProjectResources,
  sortCategories,
  type EffectCategory,
  type ProjectResource,
} from "../lib/effects";

interface Props {
  active: boolean;
}

export function ResourcePanel({ active }: Props) {
  const { state, dispatch } = useEditor();
  const pid = state.currentId;

  const [resources, setResources] = useState<ProjectResource[]>([]);
  const [favIds, setFavIds] = useState<string[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [q, setQ] = useState("");
  const [cat, setCat] = useState<EffectCategory | "">("");
  const [favOnly, setFavOnly] = useState(false);
  const [recOnly, setRecOnly] = useState(false);
  const [applying, setApplying] = useState<string | null>(null);

  const selectedClipId = state.selection?.clipId ?? null;

  const load = useCallback(
    async (opts: { q: string; cat: EffectCategory | ""; favOnly: boolean; recOnly: boolean }) => {
      if (!pid) {
        setResources([]);
        setFavIds([]);
        setCategories([]);
        return;
      }
      setLoading(true);
      try {
        const res = await listProjectResources(pid, {
          q: opts.q.trim() || undefined,
          category: opts.cat || undefined,
          favoritesOnly: opts.favOnly || undefined,
          recentOnly: opts.recOnly || undefined,
        });
        setResources(res.effects);
        setFavIds(res.favorites);
        setCategories(sortCategories(res.effects.map((e) => e.category)));
      } catch (err) {
        setResources([]);
        setFavIds([]);
        setCategories([]);
        showError(dispatch, err);
      } finally {
        setLoading(false);
      }
    },
    [pid, dispatch],
  );

  // 面板激活 / 工程切换 / 状态变化（应用效果后工程 revision 变化）时刷新
  useEffect(() => {
    if (active && pid) {
      void load({ q, cat, favOnly, recOnly });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, pid, state.revision, state.project?.revision]);

  const refresh = () => void load({ q, cat, favOnly, recOnly });

  const filtered = useMemo(
    () => resources.filter((e) => (favOnly ? e.favorite : true) && (recOnly ? e.recent : true)),
    // 服务端已按 favoritesOnly/recentOnly 过滤；本地再兜底一层（fav 高亮仍按标记）
    [resources, favOnly, recOnly],
  );

  const applyToSelected = async (effect: ProjectResource) => {
    if (!selectedClipId) return;
    if (applying) return;
    setApplying(effect.effectId);
    const st = getLatestState() || state;
    const res = await addEffectToClip(dispatch, st, {
      clipId: selectedClipId,
      effectId: effect.effectId,
      params: undefined, // 后端按默认值补齐
    });
    setApplying(null);
    if (res.ok) {
      dispatch({ type: "STATUS_SET", severity: "ok", text: `已应用「${effect.name}」到选中片段` });
    }
  };

  const toggleFav = async (effect: ProjectResource) => {
    const st = getLatestState() || state;
    await setFavorite(dispatch, st, {
      effectId: effect.effectId,
      favorite: !effect.favorite,
    });
    refresh();
  };

  return (
    <Panel title="资源库" subtitle="效果统一从这里取">
      {/* 搜索 */}
      <div className="media-filter">
        <label className="media-filter__search">
          <Search size={14} aria-hidden="true" />
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void load({ q, cat, favOnly, recOnly });
            }}
            placeholder="搜索…（如 模糊 / 旋转）"
            aria-label="搜索效果"
          />
        </label>
      </div>

      {/* 分类 tab：由 category 枚举派生 */}
      <div className="resource-tabs" role="tablist" aria-label="效果分类">
        <button
          type="button"
          role="tab"
          aria-selected={cat === ""}
          className={`resource-tabs__btn ${cat === "" ? "resource-tabs__btn--active" : ""}`}
          onClick={() => {
            setCat("");
            void load({ q, cat: "", favOnly, recOnly });
          }}
        >
          全部
        </button>
        {categories.map((c) => (
          <button
            key={c}
            type="button"
            role="tab"
            aria-selected={cat === c}
            className={`resource-tabs__btn ${cat === c ? "resource-tabs__btn--active" : ""}`}
            onClick={() => {
              setCat(c as EffectCategory);
              void load({ q, cat: c as EffectCategory, favOnly, recOnly });
            }}
          >
            {categoryLabelOf(c)}
          </button>
        ))}
      </div>

      {/* 筛选开关 */}
      <div className="resource-filters" role="group" aria-label="效果筛选">
        <button
          type="button"
          className={`resource-filters__toggle ${favOnly ? "resource-filters__toggle--on" : ""}`}
          aria-pressed={favOnly}
          onClick={() => {
            const next = !favOnly;
            setFavOnly(next);
            if (next) setRecOnly(false);
            void load({ q, cat, favOnly: next, recOnly: next ? false : recOnly });
          }}
        >
          <Star size={12} />
          只看收藏
        </button>
        <button
          type="button"
          className={`resource-filters__toggle ${recOnly ? "resource-filters__toggle--on" : ""}`}
          aria-pressed={recOnly}
          onClick={() => {
            const next = !recOnly;
            setRecOnly(next);
            if (next) setFavOnly(false);
            void load({ q, cat, favOnly: next ? false : favOnly, recOnly: next });
          }}
        >
          <Layers size={12} />
          最近使用
        </button>
      </div>

      {/* 列表 */}
      {loading ? (
        <div className="cv-loading" role="status">
          <Loader2 size={13} className="cv-spin" /> 加载中…
        </div>
      ) : resources.length === 0 ? (
        <p className="cv-empty">
          {pid ? "没有匹配的效果。试试调整搜索词或分类。" : "请先创建或打开一个工程。"}
        </p>
      ) : filtered.length === 0 ? (
        <p className="cv-empty">当前筛选下没有效果。</p>
      ) : (
        <div className="resource-grid">
          {filtered.map((e) => {
            const Icon = iconForCategory(e.category);
            const fav = favIds.includes(e.effectId) || e.favorite;
            const disabled = !selectedClipId;
            return (
              <div key={e.effectId} className="resource-card">
                <div className="resource-card__head">
                  <span className="resource-card__icon">
                    <Icon size={14} />
                  </span>
                  <span className="resource-card__name" title={e.effectId}>
                    {e.name}
                  </span>
                  <button
                    type="button"
                    className={`resource-card__fav ${fav ? "resource-card__fav--on" : ""}`}
                    aria-label={fav ? `取消收藏 ${e.name}` : `收藏 ${e.name}`}
                    aria-pressed={fav}
                    title={fav ? "取消收藏" : "收藏"}
                    onClick={() => void toggleFav(e)}
                  >
                    <Star size={13} fill={fav ? "currentColor" : "none"} />
                  </button>
                </div>
                <p className="resource-card__desc">{e.description || "（无描述）"}</p>
                {e.recent ? <span className="resource-card__tag">最近</span> : null}
                <button
                  type="button"
                  className="cv-btn cv-btn--sm cv-btn--secondary resource-card__apply"
                  disabled={disabled || applying === e.effectId}
                  onClick={() => void applyToSelected(e)}
                  title={disabled ? "请先在时间线选中一个片段" : "应用到选中片段"}
                >
                  {applying === e.effectId ? (
                    <Loader2 size={12} className="cv-spin" />
                  ) : (
                    <CheckSquare2 size={12} />
                  )}
                  应用到选中片段
                </button>
              </div>
            );
          })}
        </div>
      )}

      <p className="cv-hint" style={{ marginTop: 8 }}>
        效果栈可在右侧「效果条」里重排顺序与旁路，顺序即渲染合成顺序。
      </p>
    </Panel>
  );
}