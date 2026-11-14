import io,json,time,zlib,hashlib,unicodedata
from pathlib import Path
import uharfbuzz as hb
from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
import argparse
parser=argparse.ArgumentParser(description='Design-only font subset experiment; no production adoption')
parser.add_argument('--source',type=Path,required=True)
parser.add_argument('--baseline',type=Path,required=True,help='Full static 700 instance produced by existing _caption_font_instance')
args=parser.parse_args()
source=args.source
baseline=args.baseline
texts=['追逐梦想，一路向前','中文输入中的文字','office ffi A\u0301 café','العربية','कर्म','日本語 かな カナ 한글','甲乙\ufe00']
def shape(data,text):
 f=hb.Font(hb.Face(data)); b=hb.Buffer();b.add_str(text);b.guess_segment_properties();hb.shape(f,b)
 return [(g.codepoint,g.cluster,p.x_advance,p.y_advance,p.x_offset,p.y_offset) for g,p in zip(b.glyph_infos,b.glyph_positions)]
def build():
 t=time.perf_counter();f=TTFont(source,recalcTimestamp=False);opt=subset.Options();opt.layout_features=['*'];opt.layout_scripts=['*'];opt.retain_gids=True;opt.notdef_outline=True;opt.glyph_names=True
 ss=subset.Subsetter(options=opt);ss.populate(text=''.join(t+unicodedata.normalize('NFC',t)+unicodedata.normalize('NFD',t) for t in texts));ss.subset(f);sub=time.perf_counter()-t
 t=time.perf_counter();instantiateVariableFont(f,{'wght':700},inplace=True,optimize=False,static=True);inst=time.perf_counter()-t
 out=io.BytesIO();f.save(out);return out.getvalue(),sub,inst
raw,sub,inst=build();again,_,_=build();full=baseline.read_bytes()
print(json.dumps({'subset_s':sub,'instantiate_s':inst,'subset_bytes':len(raw),'subset_deflate':len(zlib.compress(raw,9)),'full_bytes':len(full),'full_deflate':len(zlib.compress(full,9)),'repeat_identical':raw==again,'sha256':hashlib.sha256(raw).hexdigest()}),flush=True)
for text in texts:
 a,b=shape(full,text),shape(raw,text);print(repr(text),'equal',a==b,'missing',sum(g[0]==0 for g in a),flush=True);assert a==b
assert raw == again, 'Repeated build changed bytes'
# Exact outlines/metrics for all glyphs selected by these shaping fixtures.
full_font=TTFont(io.BytesIO(full)); small_font=TTFont(io.BytesIO(raw))
used={g[0] for text in texts for g in shape(full,text)}
for gid in used:
    name=full_font.getGlyphName(gid)
    # Format-3 post names are synthesized from the surviving cmap; GID is identity.
    small_name=small_font.getGlyphName(gid)
    assert full_font['hmtx'][name]==small_font['hmtx'][small_name]
    a=full_font['glyf'][name].getCoordinates(full_font['glyf'])
    b=small_font['glyf'][small_name].getCoordinates(small_font['glyf'])
    assert a==b, name
print('outline_and_horizontal_metrics_equal',len(used))
