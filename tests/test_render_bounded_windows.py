"""Regressions for late seek clocks, bounded geometry and lossless seams."""
from __future__ import annotations

import copy
import inspect
import json
import shutil
import subprocess
import tempfile
import textwrap
import threading
import unittest
from array import array
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from cutvoke.core.httpapi import HttpApi
from cutvoke.core.model import AssetReference, Caption, Clip, Track
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderCancelled, RenderError, RenderService
from cutvoke.core.service import EditService


def r(value):
    return Rational.from_float(value)


def frames(path, width=160, height=90):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-an",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        check=True, capture_output=True).stdout
    stride = width*height*3
    return [raw[i:i+stride] for i in range(0, len(raw), stride)]


class WindowContracts(unittest.TestCase):
    def test_late_compile_origin_bounds_generated_canvas(self):
        project = EditService().create_project("late", width=160, height=90)
        project.sequence.tracks = [Track("v", "video", [
            Clip(str(i), AssetReference("one", "fake.png"), r(i), r(i+1), r(0))
            for i in range(900)])]
        renderer = RenderService()
        with patch.object(renderer, "probe_media", return_value={
                "has_video":True, "has_audio":False, "duration":0}):
            graph, sources, duration, *_ = renderer._compile(
                project.sequence, (0,0), render_window=(600.1,608.1))
        self.assertEqual(renderer._render_ctx.compile_origin, 600)
        self.assertEqual(len(sources), 1)
        self.assertLess(duration, 9)
        self.assertLess(graph.count("trim=start="), 12)

    def test_missing_outside_range_is_allowed_but_transition_neighbor_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/"source.png"; Image.new("RGB",(160,90),"red").save(source)
            missing = Path(tmp)/"missing.png"
            project = EditService().create_project("missing", width=160, height=90)
            project.sequence.tracks = [Track("v", "video", [
                Clip("present",AssetReference("a",str(source)),r(0),r(2),r(0)),
                Clip("missing",AssetReference("b",str(missing)),r(2),r(4),r(0))])]
            renderer = RenderService()
            renderer._preflight(project, str(Path(tmp)/"out.mp4"),True,output_range=(0,1))
            project.sequence.tracks[0].clips.reverse()
            project.sequence.tracks[0].clips[0].timeline_start=r(0)
            project.sequence.tracks[0].clips[0].timeline_end=r(2)
            incoming=project.sequence.tracks[0].clips[1]
            incoming.timeline_start=r(2); incoming.timeline_end=r(4)
            incoming.effects=[{"effectId":"cutvoke.transition.crossfade","params":{"duration":.5}}]
            with self.assertRaisesRegex(RenderError,"missing"):
                renderer._preflight(project,str(Path(tmp)/"out.mp4"),True,output_range=(2.1,3))

    def test_caption_only_revision_does_not_cancel_preview_but_visual_edit_does(self):
        class BlockingRenderer(RenderService):
            def __init__(self):
                super().__init__(); self.entered=threading.Event(); self.release=threading.Event()
                self.event=None; self.calls=0
            def probe_media(self,path):
                return {"has_video":True,"duration":2,"width":160,"height":90}
            def render(self,project,out_path,**kwargs):
                self.calls+=1
                if self.calls==1:
                    self.event=kwargs["cancel_event"]; self.entered.set()
                    self.release.wait(5)
                    if self.event.is_set():raise RenderCancelled("superseded")
                Path(out_path).write_bytes(b"preview")
                return {"duration":2}
        with tempfile.TemporaryDirectory() as tmp:
            service=EditService(); project=service.create_project("preview",width=160,height=90)
            source=Path(tmp)/"red.png"; Image.new("RGB",(160,90),"red").save(source)
            project.sequence.tracks=[Track("v","video",[Clip("v",AssetReference("a",str(source)),r(0),r(2),r(0))])]
            renderer=BlockingRenderer(); api=HttpApi(service,renderer,media_dir=tmp)
            failures=[]
            def call(p):
                try: api._preview_window_file(p,0)
                except Exception as exc: failures.append(exc)
            first=threading.Thread(target=call,args=(copy.deepcopy(project),)); first.start()
            self.assertTrue(renderer.entered.wait(3))
            caption=copy.deepcopy(project); caption.revision="2"; caption.name="new name"
            caption.sequence.captions=[Caption("caption","text",r(0),r(1))]
            project.name=caption.name; project.sequence.captions=caption.sequence.captions
            second=threading.Thread(target=call,args=(caption,)); second.start()
            # Identity computation completes before waiting on the cache lock.
            self.assertFalse(renderer.event.wait(.2))
            changed=copy.deepcopy(caption); changed.revision="3"
            changed.sequence.tracks[0].clips[0].effects=[{"effectId":"cutvoke.color","params":{"brightness":.1}}]
            project.sequence.tracks=copy.deepcopy(changed.sequence.tracks)
            third=threading.Thread(target=call,args=(changed,)); third.start()
            self.assertTrue(renderer.event.wait(3))
            renderer.release.set()
            for thread in (first,second,third):thread.join(5);self.assertFalse(thread.is_alive())
            self.assertTrue(any(isinstance(error,RenderCancelled) for error in failures))
            self.assertEqual(api._preview_active_jobs,{})
            api.close()


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),"FFmpeg required")
class RealWindowRendering(unittest.TestCase):
    def test_fractional_static_frames_and_end_frame_preserve_alpha_and_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);first=root/"first.png";second=root/"second.png"
            for target,color in [(first,(240,30,20,255)),(second,(20,60,240,255))]:
                image=Image.new("RGBA",(160,90),(0,0,0,0))
                ImageDraw.Draw(image).ellipse((20,10,140,80),fill=color);image.save(target)
            for fps in [Rational.of(30),Rational.of(30000,1001)]:
                project=EditService().create_project("fractional-still",width=160,height=90,fps=fps)
                a=Clip("a",AssetReference("a",str(first)),r(0),r(1),r(0))
                b=Clip("b",AssetReference("b",str(second)),r(1),r(2),r(0),effects=[
                    {"effectId":"cutvoke.transition.crossfade","params":{"duration":.2}}])
                project.sequence.tracks=[Track("v","video",[a,b])]
                renderer=RenderService();tail=None
                for moment in [.75,1.033,1.999,2.0]:
                    with self.subTest(fps=str(fps),time=moment):
                        output=root/f"still-{fps.num}-{moment}.png"
                        result=renderer.extract_still(project,moment,str(output),alpha=True)
                        self.assertTrue(result["has_alpha"])
                        image=Image.open(output).convert("RGBA")
                        self.assertLess(image.getpixel((2,2))[3],20)
                        self.assertGreater(image.getpixel((80,45))[3],220)
                        if moment==1.999:tail=image.copy()
                        if moment==2.0:
                            self.assertEqual(renderer._static_frame_time(project.sequence,1.999),
                                             renderer._static_frame_time(project.sequence,2.0))
                            # RGB below zero alpha is undefined after chroma
                            # conversion. Compare the visible composite instead.
                            bed=Image.new("RGBA",image.size,(0,0,0,255))
                            x=Image.alpha_composite(bed,image).convert("RGB").tobytes()
                            y=Image.alpha_composite(bed,tail).convert("RGB").tobytes()
                            self.assertLess(sum(abs(a-b) for a,b in zip(x,y))/len(x),1)
                thumbnail=root/f"thumb-{fps.num}.png"
                renderer.extract_frame(project,.75,str(thumbnail),size=(80,45))
                self.assertEqual(Image.open(thumbnail).size,(80,45))
                self.assertIsNone(renderer.extract_frame(project,2.0,str(root/"eof.png")))

    def test_dense_lossless_seam_matches_reference_video_audio_and_caption(self):
        class DirectRenderer(RenderService):
            def _render_in_windows(self,seq,out_path,quality,position,cancel,alpha,
                    color,primaries,video_bitrate,audio_bitrate,start,duration,warnings):
                graph,inputs,_expected,has_audio,_warnings,cwd=self._compile(
                    seq,position,alpha=alpha,render_window=(start,start+duration))
                try:
                    self._run_ffmpeg(seq,graph,inputs,out_path,quality,cancel,has_audio,
                        caption_cwd=cwd,alpha=alpha,output_start=start-self._render_ctx.compile_origin,
                        output_duration=duration)
                finally:
                    if cwd:shutil.rmtree(cwd,ignore_errors=True)
                return {"renderStrategy":"reference"}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);red=root/"red.png";blue=root/"blue.png";overlay=root/"overlay.png"
            Image.new("RGB",(160,90),"red").save(red)
            Image.new("RGB",(160,90),"blue").save(blue)
            image=Image.new("RGBA",(160,90),(0,0,0,0))
            ImageDraw.Draw(image).ellipse((50,20,110,70),fill=(255,220,20,170));image.save(overlay)
            wav=root/"tone.wav"
            subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i",
                "sine=frequency=440:sample_rate=48000:duration=12","-y",str(wav)],check=True,capture_output=True)
            project=EditService().create_project("seam",width=160,height=90)
            clips=[Clip(str(i),AssetReference(str(i),str(red if i<16 else blue)),r(i/2),r((i+1)/2),r(0)) for i in range(26)]
            clips[16].effects=[{"effectId":"cutvoke.transition.crossfade","params":{"duration":.2}}]
            upper=Clip("upper",AssetReference("o",str(overlay)),r(2),r(10),r(0))
            audio_clip=Clip("tone",AssetReference("t",str(wav)),r(0),r(12),r(0))
            audio_clip.fade_in=r(2);audio_clip.fade_out=r(2)
            project.sequence.tracks=[Track("v","video",clips),Track("o","video",[upper]),Track("a","audio",[audio_clip])]
            project.sequence.captions=[Caption("c","CLOCK",r(7),r(10))]
            actual,reference=root/"actual.mp4",root/"reference.mp4"
            result=RenderService().render(project,str(actual),quality="high")
            DirectRenderer().render(project,str(reference),quality="high")
            self.assertEqual(result["windowCount"],2)
            a,b=frames(actual),frames(reference)
            self.assertEqual(len(a),390);self.assertEqual(len(b),390)
            for i in [0,30,209,210,239,240,241,242,243,244,245,246,299,300,389]:
                with self.subTest(frame=i):
                    self.assertLess(sum(abs(x-y) for x,y in zip(a[i],b[i]))/len(a[i]),3)
            def pcm(path):
                # FFmpeg 6.1 decodes the complete final AAC block, including
                # encoder padding beyond the MP4 stream's declared end. Check
                # that end independently, then compare the exact audible span.
                metadata=json.loads(subprocess.run(["ffprobe","-v","error",
                    "-select_streams","a:0","-show_entries","stream=duration",
                    "-of","json",str(path)],check=True,capture_output=True).stdout)
                self.assertAlmostEqual(float(metadata["streams"][0]["duration"]),13,places=6)
                raw=subprocess.run(["ffmpeg","-v","error","-i",str(path),"-vn",
                    "-t","13","-ar","48000","-ac","1","-f","s16le","pipe:1"],check=True,capture_output=True).stdout
                values=array("h");values.frombytes(raw);return values
            aa,bb=pcm(actual),pcm(reference)
            self.assertEqual(len(aa),13*48000)
            self.assertEqual(len(aa),len(bb))
            # PCM16 intermediates quantize a floating-point faded source once;
            # permit that rounding while rejecting any displaced tone/phase or
            # AAC priming gap introduced by independent window encoding.
            error_rms=(sum((x-y)**2 for x,y in zip(aa,bb))/len(aa))**.5
            self.assertLess(error_rms,32)
            seam=range(round(7.9*48000),round(8.1*48000))
            self.assertLess((sum((aa[i]-bb[i])**2 for i in seam)/len(seam))**.5,32)
            self.assertEqual(max(map(abs,aa[round(12.1*48000):])),0)

    def test_general_animation_single_stream_preserves_alpha_and_legacy_trajectory(self):
        import cutvoke.core.render as module
        class LegacyRenderer(RenderService):pass
        source=textwrap.dedent(inspect.getsource(RenderService._build_animated_clip))
        source=source.replace("if len(boundaries) > 64:","if False:")
        namespace={};exec(source,module.__dict__,namespace)
        LegacyRenderer._build_animated_clip=namespace["_build_animated_clip"]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); upper=root/"upper.png"; lower=root/"lower.png"
            image=Image.new("RGBA",(160,90),(0,0,0,0));draw=ImageDraw.Draw(image)
            draw.ellipse((20,10,140,80),fill=(245,180,10,255));draw.rectangle((72,20,87,70),fill=(220,20,50,255))
            image.save(upper);Image.new("RGB",(160,90),(15,60,120)).save(lower)
            project=EditService().create_project("alpha-combo",width=160,height=90)
            clip=Clip("upper",AssetReference("a",str(upper)),r(0),r(8),r(0),effects=[
                {"effectId":"cutvoke.anim.comboRotateZoom","params":{"duration":8,
                    "primary":"rotateIn","secondary":"zoomIn","degrees":-12,"fromScale":.7}},
                {"effectId":"cutvoke.transform","params":{"scale":.6,"position":{"x":30,"y":16},"opacity":.8}}])
            project.sequence.tracks=[Track("v","video",[Clip("lower",AssetReference("b",str(lower)),r(0),r(8),r(0))]),Track("o","video",[clip])]
            renderer=RenderService();graph,*_=renderer._compile(project.sequence,(0,0))
            self.assertEqual(graph.count("perspective="),1)
            self.assertLess(graph.count("scale="),8)
            movie,reference=root/"new.mp4",root/"legacy.mp4"
            renderer.render(project,str(movie),quality="high")
            LegacyRenderer().render(project,str(reference),quality="high")
            new,old=frames(movie),frames(reference)
            self.assertEqual(len(new),240);self.assertEqual(len(old),240)
            for index in [0,30,60,90,120,180,239]:
                with self.subTest(frame=index):
                    difference=sum(abs(a-b) for a,b in zip(new[index],old[index]))/len(new[index])
                    self.assertLess(difference,5)
                    # Transparent source margins must reveal the blue lower lane.
                    self.assertGreater(new[index][2],90)

    def test_segmented_cancel_preserves_existing_target_and_cleans_intermediates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/"source.png";target=root/"existing.mp4"
            Image.new("RGB",(160,90),"red").save(source);target.write_bytes(b"original-target")
            project=EditService().create_project("cancel",width=160,height=90)
            project.sequence.tracks=[Track("v","video",[Clip(str(i),AssetReference("a",str(source)),r(i/2),r((i+1)/2),r(0)) for i in range(26)])]
            for cancel_after in [1,3]:
                with self.subTest(cancel_after_stage=cancel_after):
                    event=threading.Event()
                    class CancellingRenderer(RenderService):
                        calls=0
                        def _run_ffmpeg(self,*args,**kwargs):
                            super()._run_ffmpeg(*args,**kwargs)
                            self.calls+=1
                            if self.calls==cancel_after:event.set()
                    with self.assertRaises(RenderCancelled):
                        CancellingRenderer().render(project,str(target),overwrite=True,cancel_event=event)
                    self.assertEqual(target.read_bytes(),b"original-target")
                    self.assertEqual(list(root.glob("cutvoke-export-windows-*")),[])
            # Cancel a child that has actually started, not only a queued job.
            event=threading.Event();original_popen=subprocess.Popen;running=[]
            def start_then_cancel(command,*args,**kwargs):
                child=original_popen(command,*args,**kwargs)
                if Path(command[0]).stem.lower()=="ffmpeg":
                    running.append(child.poll() is None);event.set()
                return child
            with patch("cutvoke.core.render.subprocess.Popen",side_effect=start_then_cancel):
                with self.assertRaises(RenderCancelled):
                    RenderService().render(project,str(target),overwrite=True,cancel_event=event)
            self.assertEqual(running,[True])
            self.assertEqual(target.read_bytes(),b"original-target")
            self.assertEqual(list(root.glob("cutvoke-export-windows-*")),[])


if __name__=="__main__":unittest.main()
