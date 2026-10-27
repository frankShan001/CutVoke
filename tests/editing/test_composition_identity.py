import copy

import pytest

from cutvoke.domain.controllers import resolve
from cutvoke.domain.rational import Rational as R
from cutvoke.editing.service import EditError
from tests.support.composition_fixtures import setup


def test_sequence_clone_remaps_mattes_links_exposed_effect_paths_and_spatial_units(tmp_path):
    service,store,renderer,command=setup(tmp_path)
    try:
        command('clip.insert',clipId='second',trackId='upper',createTrackKind='video',sourcePath=str(tmp_path/'red.png'),timelineStart=R.of(0).to_json(),timelineEnd=R.of(3).to_json())
        command('clip.composite',clipId='second',compositing={'matte':{'clipId':'base'}})
        command('effect.add',clipId='base',effectId='cutvoke.color',params={'brightness':.1})
        original=service.get_project('composition');instance=original.sequence.tracks[0].clips[0].effects[0]['instanceId']
        control={'id':'exposure','label':'Exposure','clipId':'base','param':f'effect:{instance}:brightness'}
        command('composition.expose',controls=[control])
        command('clip.bind',clipId='second',param='x',binding={'source':{'type':'property','clipId':'base','param':'x'},'scale':2,'offset':3})
        command('clip.motionPath',clipId='base',points=[{'at':0,'x':2,'y':4,'outX':2},{'at':2,'x':10,'y':8,'inY':-3}])
        original=copy.deepcopy(service.get_project('composition').to_dict())
        command('sequence.duplicate',sequenceId='copy',width=192,height=128,switch=True)
        project=service.get_project('composition');base=project.sequence.tracks[0].clips[0];upper=project.sequence.tracks[1].clips[0]
        assert base.id!='base' and upper.id!='second'
        assert upper.compositing['matte']['clipId']==base.id
        assert upper.property_links['x']['source']['clipId']==base.id
        assert upper.property_links['x']['scale']==2
        assert upper.property_links['x']['offset']==6
        assert base.motion_path['points'][0]['x']==4
        assert base.motion_path['points'][0]['y']==8
        assert project.sequence.exposed_properties[0]['id']=='exposure'
        assert project.sequence.exposed_properties[0]['clipId']==base.id
        new_instance=base.effects[0]['instanceId'];assert instance!=new_instance
        assert project.sequence.exposed_properties[0]['param']==f'effect:{new_instance}:brightness'
        assert project.sequences[0].to_dict()==original['sequences'][0]
        command('history.undo')
        restored=service.get_project('composition').to_dict();restored['revision']=original['revision']
        assert restored==original
    finally:
        service.close();store.close()


def test_exact_effect_replacement_preserves_stack_position_metadata_and_is_atomic(tmp_path):
    service,store,renderer,command=setup(tmp_path)
    try:
        command('effect.add',clipId='base',effectId='cutvoke.fx.mask',params={'shape':'freehand','points':[{'x':.2,'y':.2},{'x':.8,'y':.2},{'x':.5,'y':.8}]})
        command('effect.add',clipId='base',effectId='cutvoke.fx.blur',params={'sigma':2})
        project=service.get_project('composition');fx=project.sequence.tracks[0].clips[0].effects[0];instance=fx['instanceId']
        command('effect.bypass',clipId='base',instanceId=instance,enabled=False)
        command('effect.composite',clipId='base',instanceId=instance,compositing={'opacity':.5})
        before=copy.deepcopy(service.get_project('composition').to_dict())
        command('effect.replace',clipId='base',instanceId=instance,effectId='cutvoke.fx.trackedMask',params={'points':fx['params']['points']})
        stack=service.get_project('composition').sequence.tracks[0].clips[0].effects
        assert stack[0]['effectId']=='cutvoke.fx.trackedMask'
        assert stack[0]['instanceId']==instance and stack[0]['enabled'] is False
        assert stack[0]['compositing']=={'opacity':.5}
        assert stack[1]['effectId']=='cutvoke.fx.blur'
        command('history.undo')
        current=service.get_project('composition').to_dict();current['revision']=before['revision'];assert current==before
        with pytest.raises(EditError):command('effect.replace',clipId='base',instanceId=instance,effectId='cutvoke.fx.trackedMask',params={'points':[]})
    finally:
        service.close();store.close()
