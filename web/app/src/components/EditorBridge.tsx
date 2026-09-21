/** 装配桥：绑定最新状态 + 驱动 3s 事件轮询（检测 Agent 外部修改并自动刷新）。
    纯装配，无业务逻辑。挂载时只加载工程列表（首页展示），不自动进入编辑器。 */

import { useEffect, useRef } from "react";
import { useEditor } from "../store/editor";
import { bindLatestState, loadProjects, pollEditLock, pollEvents } from "../store/actions";

export function EditorBridge() {
  const { state, dispatch } = useEditor();
  const stateRef = useRef(state);
  stateRef.current = state;

  // 绑定最新状态（每次变化，供冲突重试读取最新 revision）
  useEffect(() => {
    bindLatestState(state);
  }, [state]);

  // 挂载初始化：加载工程列表（置 serverUp，供首页「最近工程」展示）
  useEffect(() => {
    void loadProjects(dispatch);
  }, [dispatch]);

  // Agent 编辑租约轮询：工程内容仍由 events 轮询刷新，锁只负责把工作区切为只读。
  useEffect(() => {
    let alive = true;
    const tick = () => {
      if (alive) void pollEditLock(dispatch, stateRef.current);
    };
    const id = setInterval(tick, 1000);
    tick();
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [dispatch]);

  // 3 秒轮询 events：用 ref 读取最新状态，避免 interval 频繁重建
  useEffect(() => {
    let alive = true;
    const id = setInterval(() => {
      if (alive) void pollEvents(dispatch, stateRef.current);
    }, 3000);
    // 挂载后立即执行首轮
    void pollEvents(dispatch, stateRef.current);

    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [dispatch]);

  return null;
}
