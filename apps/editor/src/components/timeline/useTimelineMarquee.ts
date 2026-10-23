import { useEffect, useState, type RefObject } from "react";
import { useEditor } from "../../store/editor";
import { getLatestState } from "../../store/actions";
import { selectedClipIds } from "../../store/timelineSelection";

/** Rectangle selection begins only on empty lane space, never on a clip or trim handle. */
export function useTimelineMarquee(root: RefObject<HTMLDivElement | null>) {
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const [box,setBox] = useState<{left:number;top:number;width:number;height:number}|null>(null);
  useEffect(() => {
    const element = root.current;
    if (!element) return;
    let gesture: { id:number;x:number;y:number;base:string[];original:string[];moved:boolean } | null = null;
    let suppress = false;
    const down = (event: PointerEvent) => {
      const current = getLatestState() || state;
      const target = event.target as HTMLElement;
      if (event.button || current.editLock || current.toolMode !== "select"
        || !target.matches(".timeline-track__lane,.timeline__empty-area")) return;
      const original = selectedClipIds(current);
      gesture = { id:event.pointerId,x:event.clientX,y:event.clientY,
        base:event.ctrlKey||event.metaKey||event.shiftKey ? original : [],original,moved:false };
      element.setPointerCapture(event.pointerId);
    };
    const move = (event: PointerEvent) => {
      if (!gesture || event.pointerId !== gesture.id) return;
      if (!gesture.moved && Math.hypot(event.clientX-gesture.x,event.clientY-gesture.y)<5) return;
      gesture.moved = true;
      event.preventDefault();
      const left=Math.min(gesture.x,event.clientX),right=Math.max(gesture.x,event.clientX);
      const top=Math.min(gesture.y,event.clientY),bottom=Math.max(gesture.y,event.clientY);
      setBox({left,top,width:right-left,height:bottom-top});
      const ids = Array.from(element.querySelectorAll<HTMLElement>(".clip-block[data-clip-id]")).filter(clip=>{
        const b=clip.getBoundingClientRect();return b.right>left&&b.left<right&&b.bottom>top&&b.top<bottom;
      }).map(clip=>clip.dataset.clipId!);
      dispatch({type:"CLIP_SELECTION_SET",clipIds:[...new Set([...gesture.base,...ids])]});
    };
    const up = (event: PointerEvent) => {
      if (!gesture || event.pointerId !== gesture.id) return;
      suppress = gesture.moved;
      if (!gesture.moved && !gesture.base.length) dispatch({type:"CLIP_SELECTION_SET",clipIds:[]});
      if (element.hasPointerCapture(event.pointerId)) element.releasePointerCapture(event.pointerId);
      gesture=null;setBox(null);
    };
    const click = (event: MouseEvent) => {if(suppress){event.preventDefault();event.stopPropagation();suppress=false;}};
    const cancel = () => {if(gesture)dispatch({type:"CLIP_SELECTION_SET",clipIds:gesture.original});gesture=null;setBox(null);};
    const key = (event: KeyboardEvent) => {if(event.key==="Escape")cancel();};
    element.addEventListener("pointerdown",down,true);
    window.addEventListener("pointermove",move,true);window.addEventListener("pointerup",up,true);
    window.addEventListener("pointercancel",cancel,true);window.addEventListener("keydown",key,true);
    element.addEventListener("click",click,true);
    return ()=>{element.removeEventListener("pointerdown",down,true);window.removeEventListener("pointermove",move,true);
      window.removeEventListener("pointerup",up,true);window.removeEventListener("pointercancel",cancel,true);
      window.removeEventListener("keydown",key,true);element.removeEventListener("click",click,true);};
  },[root,dispatch,state.currentId,!!state.project]);
  return box;
}
