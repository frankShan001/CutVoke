import { msg, tr, useLocale } from "../i18n";
/** 当前工程面板（左区）：显示/重命名当前工程 + 返回工程首页。
    工程的创建、列表、打开已移到首页（HomeView）；此处不再直显 projectId。 */

import { useEffect, useState } from "react";
import { ArrowLeft, Pencil } from "lucide-react";
import { Button, Field, Panel, TextInput } from "./ui";
import { useEditor } from "../store/editor";
import { renameProject } from "../store/actions";

export function ProjectPanel() {
  useLocale();
  const { state, dispatch } = useEditor();
  const [name, setName] = useState(state.projectName);

  // 切换工程 / 名称变化时同步输入框
  useEffect(() => {
    setName(state.projectName || "");
  }, [state.projectName, state.currentId]);

  const dirty = name.trim() !== (state.projectName || "");

  const commit = () => {
    const trimmed = name.trim();
    if (!state.currentId || !trimmed || trimmed === state.projectName) {
      setName(state.projectName || "");
      return;
    }
    if (state.editLock) {
      setName(state.projectName || "");
      dispatch({
        type: "STATUS_SET",
        severity: "warn",
        text: msg("Agent {0} 正在编辑，页面暂时只读", state.editLock.owner),
      });
      return;
    }
    void renameProject(dispatch, state.currentId, trimmed);
  };

  return (
    <Panel title={tr("当前工程")}>
      <Field label={tr("工程名称")}>
        <TextInput
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") setName(state.projectName || "");
          }}
          onBlur={commit}
          placeholder={tr("给这个工程起个名字")}
          disabled={!state.currentId || Boolean(state.editLock)}
          aria-label={tr("当前工程名称")}
        />
      </Field>
      <div style={{ marginTop: 8 }}>
        <Button
          variant="secondary"
          full
          onClick={commit}
          disabled={!state.currentId || !dirty || Boolean(state.editLock)}
          aria-label={tr("保存工程名称")}
        >
          <Pencil size={14} />{tr("保存名称")}</Button>
      </div>
      <div style={{ marginTop: 8 }}>
        <Button
          variant="ghost"
          full
          onClick={() => dispatch({ type: "VIEW_SET", view: "home" })}
          aria-label={tr("返回工程列表")}
        >
          <ArrowLeft size={14} />{tr("返回工程列表")}</Button>
      </div>
    </Panel>
  );
}
