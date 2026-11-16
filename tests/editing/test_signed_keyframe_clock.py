"""Production v2 clip-clock acceptance; legacy projects retain legacy rules."""
from copy import deepcopy
import pytest
from cutvoke.domain.keyframes import Keyframe,evaluate
from cutvoke.domain.invariants import validate_project
from cutvoke.domain.protocol import Actor,Command
from cutvoke.domain.rational import Rational as R
from cutvoke.editing.service import EditService
from cutvoke.storage.store import ProjectStore

@pytest.mark.parametrize('fps',[R.of(24),R.of(30000,1001)])
@pytest.mark.parametrize('mode',['linear','ease-in','ease-out','hold','cubic-bezier'])
def test_signed_trim_extend_split_and_durable_history(tmp_path,fps,mode):
    db=str(tmp_path/'p.sqlite'); serial=0
    def apply(s,kind,**payload):
        nonlocal serial
        serial+=1
        return s.execute(Command(kind,payload,str(serial),'p',s.get_project('p').revision,Actor('human','test')))
    frame=R.of(1)/fps
    with ProjectStore(db) as store:
        s=EditService(store); p=s.create_project('p',width=160,height=90,fps=fps)
        p=s.get_project('p');p.schema_version='2';store.save(p)
        apply(s,'clip.insert',clipId='c',trackId='v',createTrackKind='video',sourcePath='synthetic.png',timelineStart=R.of(0).to_json(),timelineEnd=(frame*R.of(60)).to_json())
        for param,lo,hi in [('x',20,100),('opacity',.2,.9),('volume',.1,.8)]:
            apply(s,'clip.keyframe',clipId='c',param=param,time=R.of(0).to_json(),value=lo,interpolation=mode,**({'bezier':[1/3,0,2/3,1]} if mode=='cubic-bezier' else {}))
            apply(s,'clip.keyframe',clipId='c',param=param,time=(frame*R.of(60)).to_json(),value=hi)
        original=deepcopy(s.get_project('p').sequence.tracks[0].clips[0])
        apply(s,'clip.split',clipId='c',at=(frame*R.of(23)).to_json())
        apply(s,'clip.trim',clipId='c_r',timelineStart=(frame*R.of(30)).to_json(),timelineEnd=(frame*R.of(60)).to_json(),sourceStart=(frame*R.of(30)).to_json())
    with ProjectStore(db) as store:
        s=EditService(store); trimmed=s.get_project('p').sequence.tracks[0].clips[1]
        assert trimmed.keyframe_time_domain=='signed-local'
        assert trimmed.keyframes['x'][0].time==frame*R.of(-30)
        for param in original.keyframes:
            for i in range(31):
                assert evaluate(trimmed.keyframes[param],frame*R.of(i))==pytest.approx(evaluate(original.keyframes[param],frame*R.of(i+30)),abs=1e-9)
        apply(s,'history.undo');apply(s,'history.redo')
        assert s.get_project('p').sequence.tracks[0].clips[1].to_dict()==trimmed.to_dict()
        # Extend the right half back only to the split boundary (no overlap).
        apply(s,'clip.trim',clipId='c_r',timelineStart=(frame*R.of(23)).to_json(),timelineEnd=(frame*R.of(60)).to_json(),sourceStart=(frame*R.of(23)).to_json())
        restored=s.get_project('p').sequence.tracks[0].clips[1]
        for i in range(38):
            assert evaluate(restored.keyframes['x'],frame*R.of(i))==pytest.approx(evaluate(original.keyframes['x'],frame*R.of(i+23)),abs=1e-9)
        # Selected visible retime must not reject retained hidden neighbours.
        apply(s,'clip.keyframe',clipId='c_r',param='x',time=frame.to_json(),value=77)
        clip=s.get_project('p').sequence.tracks[0].clips[1]
        key=next(k for k in clip.keyframes['x'] if k.time==frame)
        apply(s,'clip.keyframe',clipId='c_r',action='retime',keyframeIds=[key.id],offset=frame.to_json())
        assert evaluate(s.get_project('p').sequence.tracks[0].clips[1].keyframes['x'],frame*R.of(2))==77
        # Reject signed clock in old schema even if the field is present.
        old=deepcopy(s.get_project('p'));old.schema_version='1'
        assert not validate_project(old).structurally_valid

@pytest.mark.parametrize('speed,freeze',[(R.of(1),None),(R.of(-1),None),(R.of(1),R.of(0))])
def test_right_trim_extend_and_source_slip_do_not_retime(speed,freeze):
    s=EditService();p=s.create_project('p');p.schema_version='2'
    serial=0
    def apply(kind,**payload):
        nonlocal serial
        serial+=1
        s.execute(Command(kind,payload,str(serial),'p',s.get_project('p').revision,Actor('human','test')))
    apply('clip.insert',clipId='c',trackId='v',createTrackKind='video',sourcePath='synthetic.png',timelineStart=R.of(0).to_json(),timelineEnd=R.of(5).to_json())
    c=s.get_project('p').sequence.tracks[0].clips[0];c.speed=speed;c.freeze_at=freeze
    apply('clip.keyframe',clipId='c',param='x',time=R.of(0).to_json(),value=20)
    apply('clip.keyframe',clipId='c',param='x',time=R.of(5).to_json(),value=100)
    before=deepcopy(s.get_project('p').sequence.tracks[0].clips[0].keyframes)
    apply('clip.trim',clipId='c',timelineEnd=R.of(3).to_json())
    apply('clip.trim',clipId='c',timelineEnd=R.of(5).to_json())
    apply('clip.trim',clipId='c',sourceStart=R.of(1).to_json())
    assert s.get_project('p').sequence.tracks[0].clips[0].keyframes==before


def test_trimmed_effect_range_and_nodes_restore_together():
    s=EditService();p=s.create_project('p');p.schema_version='2';n=0
    def apply(kind,**payload):
        nonlocal n
        n+=1;s.execute(Command(kind,payload,str(n),'p',s.get_project('p').revision,Actor('human','test')))
    apply('clip.insert',clipId='c',trackId='v',createTrackKind='video',sourcePath='synthetic.png',timelineStart=R.of(0).to_json(),timelineEnd=R.of(5).to_json())
    c=s.get_project('p').sequence.tracks[0].clips[0]
    c.effects=[{'effectId':'cutvoke.color','instanceId':'color1','params':{},'range':{'start':R.of(0).to_json(),'end':R.of(1).to_json()}}]
    c.keyframes={'effect:color1:brightness':[Keyframe('a',R.of(0),0),Keyframe('b',R.of(1),.5)]}
    before=deepcopy(c.to_dict())
    apply('clip.trim',clipId='c',timelineStart=R.of(2).to_json())
    clipped=s.get_project('p').sequence.tracks[0].clips[0]
    assert clipped.effects[0]['range']['start']==R.of(-2).to_json()
    apply('clip.trim',clipId='c',timelineStart=R.of(0).to_json())
    restored=s.get_project('p').sequence.tracks[0].clips[0]
    assert restored.to_dict()==before


@pytest.mark.parametrize("legacy_schema", ["1", "1.0"])
def test_direct_service_rejects_legacy_without_migration(tmp_path, legacy_schema):
    from cutvoke.editing.service import EditError
    db=str(tmp_path/'legacy.sqlite')
    with ProjectStore(db) as store:
        s=EditService(store);p=s.create_project('p');p.schema_version=legacy_schema;store.save(p)
        before=store.load('p').to_dict()
        with pytest.raises(EditError) as error:
            s.execute(Command('track.add',{'trackId':'v','kind':'video'},'edit','p',p.revision,Actor('human','test')))
        assert error.value.code=='PROJECT_CONVERSION_REQUIRED'
        assert store.load('p').to_dict()==before


def test_model_recursively_marks_v2_and_rejects_legacy_signed():
    from cutvoke.domain.model import Project,Sequence,Clip,Track,AssetReference,UnsupportedProjectSchema
    c=Clip('c',AssetReference('a','x.png'),R.of(0),R.of(1),R.of(0))
    nested=Sequence('nested',160,90,R.of(24),tracks=[Track('nv','video',[deepcopy(c)])])
    outer=deepcopy(c);outer.id='outer';outer.nested=nested
    p=Project('2','p','0',Sequence('s',160,90,R.of(24),tracks=[Track('v','video',[outer])]))
    assert p.sequence.tracks[0].clips[0].nested.tracks[0].clips[0].keyframe_time_domain=='signed-local'
    wire=p.to_dict();wire['schemaVersion']='1'
    with pytest.raises(UnsupportedProjectSchema): Project.from_dict(wire)
    wire['schemaVersion']='999'
    with pytest.raises(UnsupportedProjectSchema): Project.from_dict(wire)


def test_legacy_history_cannot_downgrade_current_project(tmp_path):
    import json
    from cutvoke.editing.service import EditError
    with ProjectStore(str(tmp_path/'history.sqlite')) as store:
        service=EditService(store);project=service.create_project('p')
        service.execute(Command('track.add',{'trackId':'v','kind':'video'},'add','p',project.revision,Actor('human','test')))
        raw=store.peek_history('p',0);old=json.loads(raw);old['schemaVersion']='1'
        store._conn.execute('UPDATE history SET snapshot_json=? WHERE project_id=? AND depth=0',(json.dumps(old),'p'))
        before=store.load('p').to_dict()
        with pytest.raises(EditError) as error:
            service.execute(Command('history.undo',{},'undo','p',before['revision'],Actor('human','test')))
        assert error.value.code=='PROJECT_CONVERSION_REQUIRED'
        assert store.load('p').to_dict()==before
        assert json.loads(store.peek_history('p',0))==old
        assert store.get_idempotent('p','undo') is None

@pytest.mark.parametrize('mode', ['insert', 'overwrite', 'overwrite-head'])
def test_insert_overwrite_rebase_effect_window_with_manual_clock(mode):
    from cutvoke.domain.model import Clip, Track, AssetReference
    s = EditService()
    c = Clip(id='c', asset_ref=AssetReference('synthetic.png'),
             timeline_start=R.of(0), timeline_end=R.of(10), source_start=R.of(0),
             keyframe_time_domain='signed-local',
             effects=[{'effectId': 'cutvoke.color', 'instanceId': 'e', 'params': {},
                       'range': {'start': R.of(6).to_json(), 'end': R.of(8).to_json()}}],
             keyframes={'effect:e:brightness': [Keyframe('a', R.of(6), 0), Keyframe('b', R.of(8), .5)]})
    original = deepcopy(c.to_dict())
    track = Track(id='v', kind='video', clips=[c])
    if mode == 'insert':
        s._insert_shift(track, R.of(5), R.of(2))
    else:
        s._overwrite_span(track, R.of(4 if mode == 'overwrite' else 0), R.of(5), 'new')
    right = track.clips[-1]
    assert [k.time for k in right.keyframes['effect:e:brightness']] == [R.of(1), R.of(3)]
    assert right.effects[0]['range'] == {'start': R.of(1).to_json(), 'end': R.of(3).to_json()}
    assert c.to_dict() == original
    if len(track.clips) == 2:
        left = track.clips[0]
        assert left.effects[0]['range'] == original['effects'][0]['range']
        assert left.effects[0] is not right.effects[0]
        right.effects[0]['params']['brightness'] = .9
        assert left.effects[0]['params'] == {}
