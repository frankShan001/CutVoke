/** 字幕几何精确编辑（H01 画布的补充）：x/y 归一化 0~1、scale 0.1~5、rotation -180~180。
    数值输入提交到 caption.update；越界由后端拒绝（runCommand 反馈错误），不前端夹紧。 */

import { useEffect, useState, type KeyboardEvent } from "react";
import { Move, Maximize2, RotateCw } from "lucide-react";
import { Field } from "./ui";
import { useEditor } from "../store/editor";
import { updateCaption } from "../store/clipEdit";
import type { Caption } from "../types/api";

interface Form {
  x: string;
  y: string;
  scale: string;
  rotation: string;
}

function toForm(c: Caption): Form {
  return {
    x: String(c.x ?? 0.5),
    y: String(c.y ?? 0.5),
    scale: String(c.scale ?? 1),
    rotation: String(c.rotation ?? 0),
  };
}

export function CaptionGeometryEditor({ caption }: { caption: Caption }) {
  const { state, dispatch } = useEditor();
  const [form, setForm] = useState<Form>(() => toForm(caption));

  useEffect(() => {
    setForm(toForm(caption));
    // 仅在选中的字幕切换时同步，不覆盖正在输入的本地值
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caption.id]);

  const commit = () => {
    const x = Number(form.x);
    const y = Number(form.y);
    const scale = Number(form.scale);
    const rotation = Number(form.rotation);
    if ([x, y, scale, rotation].some((v) => Number.isNaN(v))) {
      dispatch({ type: "STATUS_SET", severity: "warn", text: "几何值须为数字" });
      return;
    }
    void updateCaption(dispatch, state, {
      captionId: caption.id,
      x,
      y,
      scale,
      rotation,
    });
  };

  const onEnter = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      (e.target as HTMLInputElement).blur();
    }
  };

  const set = (k: keyof Form) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <div className="cv-style-edit__geom">
      <div className="cv-style-edit__hint">
        <Move size={13} /> 画布位置与变换（归一化坐标 = 像素 / 画布显示尺寸）
      </div>
      <div className="cv-geom-grid">
        <Field label="X (0~1)">
          <input
            className="cv-input"
            type="number"
            step="0.01"
            min="0"
            max="1"
            value={form.x}
            aria-label="字幕X坐标"
            onChange={set("x")}
            onBlur={commit}
            onKeyDown={onEnter}
          />
        </Field>
        <Field label="Y (0~1)">
          <input
            className="cv-input"
            type="number"
            step="0.01"
            min="0"
            max="1"
            value={form.y}
            aria-label="字幕Y坐标"
            onChange={set("y")}
            onBlur={commit}
            onKeyDown={onEnter}
          />
        </Field>
        <Field label={<><Maximize2 size={12} /> 缩放 (0.1~5)</>}>
          <input
            className="cv-input"
            type="number"
            step="0.1"
            min="0.1"
            max="5"
            value={form.scale}
            aria-label="字幕缩放"
            onChange={set("scale")}
            onBlur={commit}
            onKeyDown={onEnter}
          />
        </Field>
        <Field label={<><RotateCw size={12} /> 旋转 (±180)</>}>
          <input
            className="cv-input"
            type="number"
            step="1"
            min="-180"
            max="180"
            value={form.rotation}
            aria-label="字幕旋转"
            onChange={set("rotation")}
            onBlur={commit}
            onKeyDown={onEnter}
          />
        </Field>
      </div>
    </div>
  );
}
