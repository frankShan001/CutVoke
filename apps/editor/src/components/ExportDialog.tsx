import { msg, tr, useLocale } from "../i18n";
/** 统一导出抽屉（由顶栏「导出」按钮打开，独占模态）：成片导出 + 封面导出 + 资源包打包。 */

import { useEffect, useState } from "react";
import { CheckCircle2, X, Image as ImageIcon, Package as PackageIcon } from "lucide-react";
import { Button, Field, TextInput } from "./ui";
import { useEditor, showError } from "../store/editor";
import { exportCover, packageProject, type CoverResult, type PackageResult } from "../lib/api";
import { ExportVideoSection } from "./ExportVideoSection";

export function ExportDialog() {
  useLocale();
  const { state, dispatch } = useEditor();
  const [coverT, setCoverT] = useState("0");
  const [coverPath, setCoverPath] = useState("");
  const [coverResult, setCoverResult] = useState<CoverResult | null>(null);
  const [coverBusy, setCoverBusy] = useState(false);
  const [packPath, setPackPath] = useState("");
  const [packResult, setPackResult] = useState<PackageResult | null>(null);
  const [packBusy, setPackBusy] = useState(false);

  const close = () => dispatch({ type: "EXPORT_OPEN_SET", open: false });

  useEffect(() => {
    if (state.currentId && !coverPath) setCoverPath(`cover_${state.currentId}.png`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.currentId]);

  useEffect(() => {
    if (state.currentId && !packPath) setPackPath(`${state.currentId}.cutvokepack.zip`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.currentId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") dispatch({ type: "EXPORT_OPEN_SET", open: false });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dispatch]);

  const handleExportCover = async () => {
    if (!state.currentId) {
      showError(dispatch, { status: 400, code: "NO_PROJECT", message: tr("请先创建或选择一个工程") });
      return;
    }
    const t = Number(coverT);
    if (!coverPath.trim()) {
      showError(dispatch, { status: 400, code: "INVALID_ARGUMENT", message: tr("请填写封面输出路径") });
      return;
    }
    if (isNaN(t) || t < 0) {
      showError(dispatch, { status: 400, code: "INVALID_ARGUMENT", message: tr("时间点须为非负秒数") });
      return;
    }
    setCoverBusy(true);
    try {
      const res = await exportCover(state.currentId, coverPath.trim(), Math.round(t * 100) / 100);
      setCoverResult(res);
      dispatch({ type: "STATUS_SET", severity: "ok", text: msg("已导出封面：{0}", res.outPath) });
    } catch (err) {
      showError(dispatch, err);
    } finally {
      setCoverBusy(false);
    }
  };

  const handlePack = async () => {
    if (!state.currentId) {
      showError(dispatch, { status: 400, code: "NO_PROJECT", message: tr("请先创建或选择一个工程") });
      return;
    }
    if (!packPath.trim()) {
      showError(dispatch, { status: 400, code: "INVALID_ARGUMENT", message: tr("请填写资源包输出路径") });
      return;
    }
    setPackBusy(true);
    try {
      const res = await packageProject(state.currentId, packPath.trim());
      setPackResult(res);
      dispatch({ type: "STATUS_SET", severity: "ok", text: msg("已打包资源包：{0}", res.outPath) });
    } catch (err) {
      showError(dispatch, err);
    } finally {
      setPackBusy(false);
    }
  };

  return (
    <div className="export-dialog" role="dialog" aria-modal="true" aria-label={tr("导出成片")}>
      <div className="export-dialog__backdrop" onClick={close} />
      <section className="export-dialog__panel">
        <header className="export-dialog__head">
          <h2 className="export-dialog__title">{tr("导出成片")}</h2>
          <button className="export-dialog__close" onClick={close} aria-label={tr("关闭导出")}>
            <X size={16} />
          </button>
        </header>

        <ExportVideoSection onClose={close} />

        <div className="export-dialog__divider" role="separator" />

        <div className="export-dialog__cover">
          <div className="export-dialog__cover-head">
            <ImageIcon size={14} />{tr(" 导出封面")}</div>
          <Field label={tr("时间点 (s)")}>
            <TextInput
              type="number"
              step="any"
              min="0"
              value={coverT}
              onChange={(e) => setCoverT(e.target.value)}
              aria-label={tr("封面时间点")}
            />
          </Field>
          <Field label={tr("封面输出路径")}>
            <TextInput
              value={coverPath}
              onChange={(e) => setCoverPath(e.target.value)}
              placeholder={tr("如：cover_my_project.png")}
              aria-label={tr("封面输出路径")}
            />
          </Field>
          <Button
            variant="primary"
            full
            onClick={handleExportCover}
            disabled={coverBusy}
            style={{ marginTop: 6 }}
            aria-label={tr("导出封面")}
          >
            {coverBusy ? tr("导出中…") : tr("导出封面")}
          </Button>
          {coverResult ? (
            <div className="export-dialog__result" style={{ marginTop: 8 }}>
              <div className="export-dialog__result-head">
                <CheckCircle2 size={14} />{tr(" 封面导出完成")}</div>
              <div className="inspector__row">
                <span className="inspector__key">{tr("输出")}</span>
                <span className="inspector__val">{coverResult.outPath}</span>
              </div>
              <div className="inspector__row">
                <span className="inspector__key">{tr("尺寸")}</span>
                <span className="inspector__val">
                  {coverResult.width}×{coverResult.height}
                </span>
              </div>
            </div>
          ) : null}

          <div className="export-dialog__divider" role="separator" />

          <div className="export-dialog__cover">
            <div className="export-dialog__cover-head">
              <PackageIcon size={14} />{tr(" 打包资源包（异机交接）")}</div>
            <Field label={tr("资源包输出路径")}>
              <TextInput
                value={packPath}
                onChange={(e) => setPackPath(e.target.value)}
                placeholder={tr("如：my_project.cutvokepack.zip")}
                aria-label={tr("资源包输出路径")}
              />
            </Field>
            <Button
              variant="primary"
              full
              onClick={handlePack}
              disabled={packBusy}
              style={{ marginTop: 6 }}
              aria-label={tr("打包资源包")}
            >
              <PackageIcon size={14} />
              {packBusy ? tr("打包中…") : tr("打包资源包")}
            </Button>
            <p className="cv-hint">{tr("打包工程与引用的素材为自包含压缩包，可在另一台设备解包继续编辑（缺失素材会列为警告，不阻断）。")}</p>
            {packResult ? (
              <div className="export-dialog__result" style={{ marginTop: 8 }}>
                <div className="export-dialog__result-head">
                  <CheckCircle2 size={14} />{tr(" 打包完成")}</div>
                <div className="inspector__row">
                  <span className="inspector__key">{tr("输出")}</span>
                  <span className="inspector__val">{packResult.outPath}</span>
                </div>
                <div className="inspector__row">
                  <span className="inspector__key">{tr("片段")}</span>
                  <span className="inspector__val">{packResult.clipCount}</span>
                </div>
                <div className="inspector__row">
                  <span className="inspector__key">{tr("素材")}</span>
                  <span className="inspector__val">{packResult.assetCount}</span>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </section>
    </div>
  );
}
