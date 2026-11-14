from pathlib import Path
import subprocess,hashlib,json,unicodedata,time,argparse,platform
from importlib.metadata import version
from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
parser=argparse.ArgumentParser(description='Synthetic-only libass pixel probe, not production acceptance')
parser.add_argument('--source',type=Path,required=True)
parser.add_argument('--output-dir',type=Path,required=True,help='New directory; existing directories are rejected')
args=parser.parse_args()
source=args.source
if hashlib.sha256(source.read_bytes()).hexdigest()!='d68bafcb48a2707749396aa12bbbd833cb70401f3a9a689fd2902c7e0d295964':
 raise SystemExit('Unexpected font source SHA256')
root=args.output_dir;root.mkdir(parents=True,exist_ok=False)
print(json.dumps({'python':platform.python_version(),'fonttools':version('fonttools'),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}))
print(subprocess.run(['ffmpeg','-version'],capture_output=True,text=True,check=True).stdout.splitlines()[0])
texts=['追逐梦想，一路向前','中文输入中的文字','office ffi A\u0301 café','العربية','कर्म','日本語 かな カナ 한글','甲乙\ufe00']
for weight in [400,700]:
 for mode in ['full','subset']:
  out=root/f'{weight}-{mode}';out.mkdir(exist_ok=True);(out/'fonts').mkdir()
  f=TTFont(source,recalcTimestamp=False);t=time.perf_counter()
  if mode=='subset':
   opt=subset.Options();opt.layout_features=['*'];opt.layout_scripts=['*'];opt.retain_gids=True;opt.notdef_outline=True;opt.glyph_names=True
   ss=subset.Subsetter(options=opt);ss.populate(text=''.join(t+unicodedata.normalize('NFC',t)+unicodedata.normalize('NFD',t) for t in texts));ss.subset(f)
  instantiateVariableFont(f,{'wght':weight},inplace=True,optimize=False,static=True)
  f['OS/2'].usWeightClass=weight;f['OS/2'].fsSelection=(f['OS/2'].fsSelection&~(0x20|0x40))|(0x20 if weight==700 else 0x40);f['head'].macStyle=(f['head'].macStyle&~1)|int(weight==700)
  for n,v in {1:'CutVoke Pixel Probe',16:'CutVoke Pixel Probe',2:'Bold' if weight==700 else 'Regular',17:'Bold' if weight==700 else 'Regular',4:'CutVoke Pixel Probe',6:'CutVokePixelProbe'}.items():
   for platform,encoding,lang in {(x.platformID,x.platEncID,x.langID) for x in f['name'].names if x.nameID==n}|{(3,1,0x409)}: f['name'].setName(v,n,platform,encoding,lang)
  f.save(out/'fonts/font.ttf'); print(weight,mode,'seconds',time.perf_counter()-t,flush=True)
  ass='[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,CutVoke Pixel Probe,32,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,'+('-1' if weight==700 else '0')+',0,0,0,100,100,0,0,1,2,0,7,20,20,20,1\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
  for i,text in enumerate(texts): ass+=f'Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,{{\\pos(20,{20+i*85})}}'+text+'\n'
  (out/'sample.ass').write_text(ass)
  p=subprocess.run(['ffmpeg','-hide_banner','-y','-f','lavfi','-i','color=c=black:s=1280x720:d=1','-vf',f"subtitles={out}/sample.ass:fontsdir={out}/fonts",'-frames:v','1','-pix_fmt','rgba','-f','rawvideo',str(out/'frame.rgba')],capture_output=True)
  (out/'ffmpeg.log').write_bytes(p.stderr)
  if p.returncode:raise SystemExit(p.stderr.decode())
 a=(root/f'{weight}-full/frame.rgba').read_bytes();b=(root/f'{weight}-subset/frame.rgba').read_bytes()
 print(json.dumps({'weight':weight,'identical':a==b,'changed_bytes':sum(x!=y for x,y in zip(a,b)),'full_sha256':hashlib.sha256(a).hexdigest(),'subset_sha256':hashlib.sha256(b).hexdigest()}),flush=True)

 if len(a)!=1280*720*4 or a!=b: raise SystemExit('Pixel mismatch or unexpected RGBA length')
