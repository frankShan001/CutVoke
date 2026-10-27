import copy

import numpy as np
import pytest

from cutvoke.domain.model import Sequence, Track, Clip, AssetReference, Project
from cutvoke.domain.rational import Rational as R
from cutvoke.domain.essential_properties import at_path
from cutvoke.editing.service import EditError
from tests.support.composition_fixtures import setup, frames


def test_shared_master_overrides_cache_and_undo_remain_independent(tmp_path):
    service, store, renderer, command = setup(tmp_path)
    try:
        project = service.get_project("composition")
        child = copy.deepcopy(project.sequence)
        child.id, child.name = "master", "Master"
        child.tracks[0].id, child.tracks[0].clips[0].id = "master_v", "picture"
        child.tracks[0].clips[0].timeline_end = R.of(1)
        project.sequence.tracks = []
        project.sequences.append(child)
        store.save(project)
        command("composition.expose", sequenceId="master", controls=[{"id": "visibility", "label": "Visibility", "clipId": "picture", "param": "opacity"}])
        command("composition.instance", clipId="first", sequenceId="master", trackId="instances", timelineEnd=1)
        command("composition.instance", clipId="second", sequenceId="master", trackId="instances", timelineStart=1, timelineEnd=2)
        command("composition.override", clipId="second", values={"visibility": .25})
        before = copy.deepcopy(service.get_project("composition").to_dict())
        output = tmp_path / "instances.mp4"
        renderer.render(service.get_project("composition"), str(output), quality="high")
        rgb = frames(output)
        assert rgb[3, 20, 20, 0] > 170
        assert 35 < rgb[13, 20, 20, 0] < 60
        assert service.get_project("composition").to_dict() == before
        assert not service.get_project("composition").sequences[1].tracks[0].clips[0].effects
        command("history.undo")
        assert not store.load("composition").sequence.tracks[0].clips[1].property_overrides
        snapshot = copy.deepcopy(store.load("composition").to_dict())
        with pytest.raises(EditError):
            command("composition.override", clipId="second", values={"visibility": 99})
        assert store.load("composition").to_dict() == snapshot
    finally:
        service.close(); store.close()


def test_composition_cycle_rejected_and_nested_edits_are_atomic_local_commands(tmp_path):
    service, store, renderer, command = setup(tmp_path)
    try:
        command("clip.group", clipIds=["base"], groupId="group")
        project = service.get_project("composition")
        container = project.sequence.tracks[0].clips[0]
        path = [container.id]
        child = container.nested.tracks[0].clips[0]
        command("composition.edit", path=path, command={"type": "effect.add", "payload": {"clipId": child.id, "effectId": "cutvoke.fx.invert", "params": {}}})
        assert at_path(store.load("composition"), path).sequence.tracks[0].clips[0].effects[0]["effectId"] == "cutvoke.fx.invert"
        command("history.undo")
        assert not at_path(store.load("composition"), path).sequence.tracks[0].clips[0].effects
        snapshot = copy.deepcopy(store.load("composition").to_dict())
        with pytest.raises(EditError):
            command("composition.instance", sequenceId=project.sequence.id, timelineStart=4, timelineEnd=5)
        assert store.load("composition").to_dict() == snapshot
    finally:
        service.close(); store.close()


def test_deep_shared_compositions_render_with_the_same_documented_depth_bound(tmp_path):
    service,store,renderer,command=setup(tmp_path)
    try:
        project=service.get_project('composition')
        master=copy.deepcopy(project.sequence);master.id='master'
        master.tracks[0].clips[0].timeline_end=R.of(1)
        project.sequences=[master]
        for index in range(6):
            child=Sequence(id=f'level_{index}',width=96,height=64,fps=R.of(10),tracks=[Track(f'v{index}','video',[
                Clip(f'instance{index}',AssetReference('',''),R.of(0),R.of(1),R.of(0),sequence_ref=project.sequences[-1].id)])])
            project.sequences.append(child)
        project.sequence=project.sequences[-1];project.active_sequence_id=project.sequence.id
        store.save(project)
        output=tmp_path/'deep.mp4';renderer.render_preview_window(service.get_project('composition'),str(output),0,.3)
        assert frames(output)[1,20,20,0]>160
    finally:
        service.close();store.close()


def test_exposed_override_wins_master_binding_and_path_without_removing_other_axis(tmp_path):
    from cutvoke.domain.essential_properties import resolve as resolve_compositions
    from cutvoke.domain.spatial_path import resolve as resolve_paths
    from cutvoke.domain.keyframes import evaluate
    service,store,renderer,command=setup(tmp_path)
    try:
        command('clip.motionPath',clipId='base',points=[{'at':0,'x':0,'y':0},{'at':2,'x':16,'y':8}])
        command('clip.bind',clipId='base',param='opacity',binding={'source':{'type':'sine','amplitude':.4,'value':.5}})
        project=service.get_project('composition');master=copy.deepcopy(project.sequence);master.id='master'
        master.exposed_properties=[{'id':'horizontal','clipId':'base','param':'x'},{'id':'visibility','clipId':'base','param':'opacity'}]
        project.sequences.append(master);project.sequence.tracks=[];store.save(project)
        command('composition.instance',clipId='instance',sequenceId='master',timelineEnd=2)
        command('composition.override',clipId='instance',values={'horizontal':3,'visibility':.25})
        snapshot=resolve_compositions(service.get_project('composition'))
        parent=snapshot.sequence.tracks[0].clips[0]
        child=copy.deepcopy(snapshot);child.sequence=parent.nested
        child=resolve_paths(child);clip=child.sequence.tracks[0].clips[0]
        assert 'opacity' not in clip.property_links and 'x' not in clip.keyframes
        assert clip.transform()['position']['x']==3
        assert float(evaluate(clip.keyframes['y'],R.of(1)))==4
        output=tmp_path/'override.mp4';renderer.render(service.get_project('composition'),str(output),quality='high')
        rgb=frames(output)
        assert all(35<rgb[index,20,20,0]<60 for index in (0,5,10,15))
        assert master.tracks[0].clips[0].property_links
    finally:
        service.close();store.close()
