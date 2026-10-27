import copy
import time
from pathlib import Path

import pytest
from PIL import Image

from cutvoke.domain.protocol import Command
from cutvoke.editing.service import EditService, EditError
from cutvoke.editing.effect_modules import EffectModuleLibrary
from cutvoke.interfaces.mcp_server import MCPServer
from cutvoke.storage.project_directory import ProjectLibraryStore


def test_legacy_luma_module_retains_its_content_identity(tmp_path):
    from cutvoke.editing.effect_modules import encoded
    library = EffectModuleLibrary(tmp_path/'modules')
    document = library.document({'effects':[{'effectId':'cutvoke.transition.lumaReveal','params':{}}]})
    assert not {'spatialBlur','temporalFrames','dissolveMix'} & document['recipe']['effects'][0]['params'].keys()
    assert document['recipe']['effects'][0]['params'] == {
        'duration':.6,'softness':.08,'reverse':False,'keySource':'outgoing','curve':'smooth'}
    module_id = library.identity(document)
    library.root.mkdir()
    (library.root/(module_id+'.json')).write_bytes(encoded(document))
    assert library.load(module_id) == document
    exported = library.export(module_id)
    assert EffectModuleLibrary(tmp_path/'other').import_package(exported['path'])['moduleId'] == module_id


def test_duplicate_effects_have_precise_update_remove_bypass_and_order_with_undo(tmp_path):
    store = ProjectLibraryStore(str(tmp_path/'library.sqlite')); service = EditService(store)
    project = service.create_project('p'); image = tmp_path/'source.png'; Image.new('RGB',(32,32),'blue').save(image)
    def apply(kind, **payload):
        return service.execute(Command(kind,payload,project_id='p',expected_revision=service.get_project('p').revision))
    try:
        apply('clip.insert',trackId='v',createTrackKind='video',clipId='c',sourcePath=str(image))
        first = apply('effect.add',clipId='c',effectId='cutvoke.fx.blur',params={'sigma':1}).changed_entities[0]['instanceId']
        second = apply('effect.add',clipId='c',effectId='cutvoke.fx.blur',params={'sigma':2}).changed_entities[0]['instanceId']
        apply('effect.update',clipId='c',instanceId=second,params={'sigma':3})
        apply('effect.bypass',clipId='c',instanceId=first,enabled=False)
        apply('effect.reorder',clipId='c',instanceOrder=[second,first])
        effects = service.get_project('p').sequence.tracks[0].clips[0].effects
        assert [e['instanceId'] for e in effects] == [second,first]
        assert effects[0]['params']['sigma'] == 3 and effects[1]['enabled'] is False
        apply('effect.remove',clipId='c',instanceId=first)
        assert len(service.get_project('p').sequence.tracks[0].clips[0].effects) == 1
        apply('history.undo')
        assert len(service.get_project('p').sequence.tracks[0].clips[0].effects) == 2
        apply('effect.remove',clipId='c',effectId='cutvoke.fx.blur')
        assert not service.get_project('p').sequence.tracks[0].clips[0].effects
    finally:
        service.close(); store.close()


@pytest.mark.parametrize('effect_id',['cutvoke.fx.lightWeave','cutvoke.fx.opticalField'])
def test_optical_modules_roundtrip_numeric_controls_and_reject_private_fields(tmp_path,effect_id):
    library = EffectModuleLibrary(tmp_path/'modules')
    recipe = {'effects':[{'effectId':effect_id,'params':{'anchorFrames':[{'time':0,'x':.1,'y':.2},{'time':1,'x':.8,'y':.5}]}}]}
    assert effect_id in library.supported()
    saved = library.save(recipe)
    exported = library.export(saved['moduleId'])
    imported = EffectModuleLibrary(tmp_path/'other').import_package(exported['path'])
    assert imported['moduleId'] == saved['moduleId']
    for key in ('sourcePath','text','assetId','image'):
        invalid=copy.deepcopy(recipe); invalid['effects'][0]['params']['anchorFrames'][0][key]='PRIVATE'
        with pytest.raises(ValueError): library.save(invalid)


def test_batch_limit_and_atomic_lease_release_status_are_explicit(tmp_path):
    store = ProjectLibraryStore(str(tmp_path/'library.sqlite')); service = EditService(store); server = MCPServer(service)
    try:
        service.create_project('p')
        result=server.call_tool('media_import',{'projectId':'p','paths':['missing.mp4']*48})
        assert not result['ok'] and 'maximum 32' in result['error']['message'] and '48' in result['error']['message']
        current=server.call_tool('edit_lock',{'projectId':'p','action':'acquire','owner':'first'})['lease']
        denied=server.call_tool('edit_lock',{'projectId':'p','action':'release','leaseId':'other'})
        assert denied['status']=='not-owner' and denied['locked'] and not denied['released']
        expired_store=store._target('p')
        expired_store._conn.execute('UPDATE edit_leases SET expires_at=?',(time.time()-1,))
        expired=server.call_tool('edit_lock',{'projectId':'p','action':'release','leaseId':current['leaseId']})
        assert expired['status']=='expired' and not expired['locked'] and not expired['released']
        absent=server.call_tool('edit_lock',{'projectId':'p','action':'release','leaseId':current['leaseId']})
        assert absent['status']=='not-held' and not absent['locked']
        lease=server.call_tool('edit_lock',{'projectId':'p','action':'acquire','owner':'first'})['lease']
        released=server.call_tool('edit_lock',{'projectId':'p','action':'release','leaseId':lease['leaseId']})
        assert released['status']=='released' and released['released'] and not released['locked']
    finally:
        server.close(); store.close()
