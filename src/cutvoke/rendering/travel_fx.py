"""Travel camera transitions and bounded temporal shutter blur.

These are native declarative effects: the registry exposes the same parameters
to MCP, Web, preview, undo/history and final export. No baked transition media.
"""
from __future__ import annotations


def builtin_travel_effects():
    from cutvoke.rendering.effects import EffectSpec
    def number(default, low, high, label):
        return {"type": "number", "default": default, "minimum": low, "maximum": high,
                "description": {"zh-CN": label}}
    common = {"duration": number(.35, .05, 2, "转场时长（秒）")}
    result = []
    for leaf, cn, en, pattern, props, words in [
        ("whipPan", "快速甩镜", "Whip pan", "whip",
         {"direction": {"type": "string", "enum": ["left", "right"], "default": "left",
                        "description": {"zh-CN": "镜头甩动方向"}},
          "blur": number(.65, 0, 1, "快门模糊强度")},
         ("甩镜", "疾风", "whip", "camera")),
        ("zoomOut", "拉远穿梭", "Zoom-out handoff", "zoom",
         {"zoom": number(1.8, 1, 3, "拉远倍率")}, ("拉远", "穿梭", "zoom")),
        ("lightLeak", "暖色漏光叠化", "Warm light leak", "leak",
         {"strength": number(.72, 0, 1, "暖色漏光强度")}, ("漏光", "暖色", "胶片", "light leak")),
    ]:
        result.append(EffectSpec(
            id="cutvoke.transition."+leaf, version="1.0.0",
            name={"zh-CN": cn, "en": en}, category="transition",
            description={"zh-CN": cn+"；随转场进度变化，首尾保留清晰原画面。"},
            license="MIT", inputs=("video:a", "video:b"), output="video",
            parameters={"type": "object", "properties": {**common, **props}, "additionalProperties": False},
            implementation={"engine": "ffmpeg-xfade", "transition": "custom", "cameraPattern": pattern},
            source="builtin", keywords=words, applies_to=("image", "video"), subcategory="运镜"))
    result.append(EffectSpec(
        id="cutvoke.fx.motionBlur", version="1.0.0",
        name={"zh-CN": "快门运动模糊", "en": "Temporal shutter blur"}, category="fx",
        description={"zh-CN": "混合连续三帧模拟快门曝光，支持限定片段内起止时间；适合变速高速段。"},
        license="MIT", inputs=("video",), output="video",
        parameters={"type": "object", "properties": {
            "strength": number(.5, 0, .9, "快门混合强度"),
            "start": number(0, 0, 3600, "片段内起始秒"),
            "end": number(3600, 0, 3600, "片段内结束秒")}, "additionalProperties": False},
        implementation={"engine": "internal", "filter": "travelMotionBlur"},
        source="builtin", keywords=("运动模糊", "快门", "变速", "motion blur"),
        applies_to=("image", "video"), subcategory="动感"))
    result.append(EffectSpec(
        id="cutvoke.fx.sidechainDuck", version="1.0.0",
        name={"zh-CN":"环境声侧链下压","en":"Keyed ambience ducking"},category="fx",
        description={"zh-CN":"用指定人声/ASMR片段的真实音量触发音乐下压；保留原声，限制最大衰减并保留-2dB主混音余量。"},
        license="MIT",inputs=("audio",),output="audio",subcategory="音频",
        parameters={"type":"object","properties":{
            "keyClipIds":{"type":"array","minItems":1,"maxItems":64,"items":{"type":"string"},"description":{"zh-CN":"触发下压的人声或ASMR片段ID"}},
            "reductionDb":number(-14,-24,-3,"最大音乐衰减（dB）"),
            "thresholdDb":number(-40,-60,-10,"侧链触发阈值（dB）"),
            "attackMs":number(10,1,100,"音乐下压起音（毫秒）"),
            "releaseMs":number(300,50,1000,"音乐恢复（毫秒）")},"required":["keyClipIds"],"additionalProperties":False},
        implementation={"engine":"internal","filter":"sidechainDuck"},source="builtin",
        keywords=("侧链","下压","ASMR","ducking"),applies_to=("audio","video")))
    return result

def build_sidechain_ducks(groups, labels_by_clip, requests, parts, duration):
    """Wire real audio key streams without consuming audible originals."""
    active=[]
    for target,params in requests:
        refs=[labels_by_clip[x] for x in params.get("keyClipIds",[]) if x in labels_by_clip
              and labels_by_clip[x]!=target]
        if refs: active.append((target,params,refs))
    consumers={}
    for i,(_,_,refs) in enumerate(active):
        for j,ref in enumerate(refs):consumers.setdefault(ref,[]).append((i,j))
    remap={}
    for n,(ref,uses) in enumerate(consumers.items()):
        audible=f"duckaud{n}";remap[ref]=audible
        outputs=[audible]+[f"duckkey{i}_{j}" for i,j in uses]
        parts.append(f"[{ref}]asplit={len(outputs)}"+"".join(f"[{x}]" for x in outputs))
    for i,(target,p,refs) in enumerate(active):
        side=f"duckside{i}"
        # FFmpeg's sidechain framesync can stop with buffered samples still
        # unread when an input reaches EOF. Supply a bounded silent drain tail
        # to BOTH inputs, then retain only the authoritative project interval.
        padded_duration = duration + 1
        ins="".join(f"[duckkey{i}_{j}]" for j in range(len(refs)))
        if len(refs)>1:
            parts.append(f"{ins}amix=inputs={len(refs)}:normalize=0:duration=longest,apad=whole_dur={padded_duration:.9f}[{side}]")
        else:
            parts.append(f"{ins}apad=whole_dur={padded_duration:.9f}[{side}]")
        floor=10**(float(p.get("reductionDb",-14))/20)
        threshold=10**(float(p.get("thresholdDb",-40))/20)
        attack=float(p.get("attackMs",10));release=float(p.get("releaseMs",300))
        parts.append(f"[{target}]asplit=2[duckdry{i}][duckwet{i}]")
        parts.append(f"[duckwet{i}]apad=whole_dur={padded_duration:.9f}[duckwetpad{i}]")
        parts.append(f"[duckwetpad{i}][{side}]sidechaincompress=threshold={threshold:.9f}:ratio=20:"
                     f"attack={attack:.4f}:release={release:.4f}:level_sc=4:makeup=1,"
                     f"atrim=duration={duration:.9f}[duckcomp{i}]")
        parts.append(f"[duckcomp{i}][duckdry{i}]amix=inputs=2:weights='{1-floor:.9f} {floor:.9f}':"
                     f"normalize=0:duration=longest[duckout{i}]")
        remap[target]=f"duckout{i}"
    for name,labels in groups:
        mapped=[remap.get(x,x) for x in labels]
        if len(mapped)==1:parts.append(f"[{mapped[0]}]anull[{name}]")
        else:parts.append("".join(f"[{x}]" for x in mapped)+f"amix=inputs={len(mapped)}:duration=longest:normalize=0[{name}]")
    return bool(requests)



def motion_blur_expr(params: dict) -> str:
    strength = max(0, min(.9, float(params.get("strength", .5))))
    start = max(0, float(params.get("start", 0)))
    end = max(start, float(params.get("end", 3600)))
    if strength <= 0 or end <= start:
        return "null"
    return f"tmix=frames=3:weights='{strength/2:.6f} {strength/2:.6f} {1-strength:.6f}'"


def build_motion_blur(params, cur, parts, counter):
    # tmix's generic timeline bypass returns an old ring-buffer frame. Select
    # between an untouched branch and the continuously warmed shutter branch
    # instead; disabled frames must remain at their original timestamps.
    stem=f"travelblur{counter[0]}"; counter[0]+=1
    start=max(0, float(params.get("start",0)))
    end=max(start, float(params.get("end",3600)))
    parts.append(f"[{cur}]split=2[{stem}base][{stem}input]")
    parts.append(f"[{stem}input]{motion_blur_expr(params)}[{stem}mix]")
    parts.append(f"[{stem}base][{stem}mix]blend=all_expr='if(between(T,{start:.9f},{end:.9f}),B,A)':shortest=1[{stem}out]")
    return f"{stem}out"


def camera_transition_expr(pattern: str, params: dict, *, has_alpha: bool = False) -> str:
    """Spatial samples on full-resolution YUV planes; P runs from 1 to 0."""
    def sample(source, x, y, radius="0"):
        def plane(i):
            terms = [f"{weight}*{source}{i}(clip(({x})+({radius})*{k},0,W-1),clip({y},0,H-1))"
                     for k, weight in [(-1, 1), (0, 2), (1, 1)]]
            return "("+"+".join(terms)+")/4"
        if has_alpha:
            return f"if(eq(PLANE,0),{plane(0)},if(eq(PLANE,1),{plane(1)},if(eq(PLANE,2),{plane(2)},{plane(3)})))"
        return f"if(eq(PLANE,0),{plane(0)},if(eq(PLANE,1),{plane(1)},{plane(2)}))"
    prefix = "st(0,1-P);st(1,ld(0)*ld(0)*(3-2*ld(0)));st(2,4*P*(1-P));"
    if pattern == "whip":
        right = params.get("direction", "left") == "right"
        direction = -1 if right else 1
        shift = f"{direction}*W*ld(1)"
        incoming = f"{direction}*W*(ld(1)-1)"
        radius = f"W*ld(2)*{float(params.get('blur', .65))*.09:.6f}"
        a = sample("a", f"X+{shift}", "Y", radius)
        b = sample("b", f"X+{incoming}", "Y", radius)
        condition = f"gte(X+({shift}),0)*lt(X+({shift}),W)"
        return prefix + f"if({condition},{a},{b})"
    if pattern == "zoom":
        zoom = max(1, min(3, float(params.get("zoom", 1.8))))
        a = sample("a", f"(X-W/2)*(1+{zoom-1:.6f}*ld(1))+W/2",
                   f"(Y-H/2)*(1+{zoom-1:.6f}*ld(1))+H/2", "W*ld(2)*.009")
        b = sample("b", "(X-W/2)/(1+.25*P)+W/2", "(Y-H/2)/(1+.25*P)+H/2", "W*ld(2)*.006")
        return prefix+f"({a})*P+({b})*(1-P)"
    if pattern == "leak":
        strength = max(0, min(1, float(params.get("strength", .72))))
        light = (f"ld(2)*{strength:.6f}*min(1,"
                 "exp(-pow((X/W-(.08+.4*ld(1)))/.32,2))+"
                 ".55*exp(-pow((Y/H-.15)/.18,2)))")
        tint = "if(eq(PLANE,0),235,if(eq(PLANE,1),96,160))"
        color = f"(A*P+B*(1-P))*(1-({light}))+({tint})*({light})"
        if has_alpha:
            color = f"if(eq(PLANE,3),A*P+B*(1-P),{color})"
        return prefix + color
    raise ValueError(f"unknown camera transition pattern: {pattern}")

def build_cached_smooth_mask(params, cur, parts, counter):
    """Cache frame-invariant path coverage; multiply it with each source alpha."""
    import hashlib,json,tempfile
    from pathlib import Path
    from PIL import Image,ImageDraw,ImageFilter
    from cutvoke.rendering.render import _smooth_closed_path
    digest=hashlib.sha256(json.dumps(params,sort_keys=True).encode()).hexdigest()
    folder=Path(tempfile.gettempdir())/"cutvoke-path-mattes-v1";folder.mkdir(exist_ok=True)
    path=folder/(digest+".png")
    if not path.exists():
        size=1024
        pts=[(float(p["x"]),float(p["y"])) for p in params["points"]]
        pts=_smooth_closed_path(pts)
        mask=Image.new("L",(size,size),0);draw=ImageDraw.Draw(mask)
        draw.polygon([(x*(size-1),y*(size-1)) for x,y in pts],fill=255)
        feather=float(params.get("feather",0))
        if feather:mask=mask.filter(ImageFilter.GaussianBlur(feather*size))
        if params.get("invert"):
            from PIL import ImageOps
            mask=ImageOps.invert(mask)
        import uuid
        temporary=folder/(digest+"-"+uuid.uuid4().hex+".png")
        mask.save(temporary);temporary.replace(path)
    escaped=str(path).replace("\\","/").replace(":","\\:").replace("'","\\'")
    stem=f"pathcache{counter[0]}";counter[0]+=1
    parts.append(f"[{cur}]format=rgba,split=3[{stem}src][{stem}alpha][{stem}ref]")
    parts.append(f"movie=filename='{escaped}':dec_threads=1,format=gray[{stem}matte]")
    parts.append(f"[{stem}matte][{stem}ref]scale2ref=w=iw:h=ih[{stem}sized][{stem}unused]")
    parts.append(f"[{stem}unused]nullsink")
    parts.append(f"[{stem}alpha]alphaextract[{stem}original]")
    parts.append(f"[{stem}original][{stem}sized]blend=all_expr='A*B/255':shortest=0[{stem}combined]")
    parts.append(f"[{stem}src][{stem}combined]alphamerge[{stem}out]")
    return f"{stem}out"
