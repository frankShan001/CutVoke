"""Public-command recipe checks; no new editing backend or rendering claims."""
import copy
import importlib.util
from pathlib import Path
import unittest
from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.rational import Rational
from cutvoke.domain.protocol import Actor, Command
from cutvoke.editing.service import EditService, EditError

spec = importlib.util.spec_from_file_location('transition_recipe', Path(__file__).parents[2] / 'examples/selected-transitions/recipe.py')
recipe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recipe)

class SelectedTransitionRecipeTests(unittest.TestCase):
    def setUp(self):
        self.service = EditService()
        self.project = self.service.create_project('selected-transition-test')
        self.project.sequence.tracks = [Track('video', 'video', [
            Clip(str(i), AssetReference('image', 'synthetic.png'), Rational.of(i), Rational.of(i+1), Rational.of(0))
            for i in range(5)])]

    def command(self, kind, payload, identity):
        current = self.service.get_project(self.project.project_id)
        return Command(type=kind, payload=payload, command_id=identity,
                       project_id=current.project_id, expected_revision=current.revision,
                       actor=Actor('agent', 'recipe-test'))

    def test_selected_boundaries_preview_commit_idempotence_and_undo(self):
        before = copy.deepcopy(self.project.sequence.to_dict())
        payload = recipe.selected_transition_batch(self.project, ['0', '1', '3', '4'])
        self.assertEqual([c['payload']['clipId'] for c in payload['commands']], ['1', '4'])
        request = self.command('edit.batch', payload, 'apply')
        preview = self.service.preview_command(request)
        self.assertEqual(len(preview['changedEntities']), 2)
        self.assertEqual(self.project.sequence.to_dict(), before)
        result = self.service.execute(request)
        self.assertEqual(len(result.changed_entities), 2)
        self.assertEqual(self.service.execute(request).revision, result.revision)
        after = copy.deepcopy(self.service.get_project(self.project.project_id).sequence.to_dict())
        for clip in after['tracks'][0]['clips']:
            if clip['id'] in ('1', '4'):
                self.assertEqual(len(clip['effects']), 1)
                clip['effects'] = []
        self.assertEqual(after, before)  # clocks, source and audio fields unchanged
        self.service.execute(self.command('history.undo', {}, 'undo'))
        self.assertEqual(self.service.get_project(self.project.project_id).sequence.to_dict(), before)

    def test_late_invalid_boundary_rolls_back_whole_batch(self):
        before = copy.deepcopy(self.project.sequence.to_dict())
        payload = recipe.selected_transition_batch(self.project, ['0', '1'])
        payload['commands'].append({'type': 'effect.setTransition', 'payload': {
            'clipId': '0', 'effectId': 'cutvoke.transition.crossfade', 'params': {'duration': .25}}})
        with self.assertRaises(EditError):
            self.service.execute(self.command('edit.batch', payload, 'bad'))
        self.assertEqual(self.service.get_project(self.project.project_id).sequence.to_dict(), before)

    def test_gaps_existing_transitions_locked_and_stale_selection(self):
        clips = self.project.sequence.tracks[0].clips
        clips[2].timeline_start = Rational.of(5, 2)
        clips[4].effects = [{'effectId': 'cutvoke.transition.crossfade', 'params': {'duration': .4}}]
        payload = recipe.selected_transition_batch(self.project, ['0', '1', '2', '3', '4'])
        self.assertEqual([c['payload']['clipId'] for c in payload['commands']], ['1', '3'])
        self.project.sequence.tracks[0].locked = True
        with self.assertRaises(ValueError): recipe.selected_transition_batch(self.project, ['0', '1'])
        with self.assertRaises(ValueError): recipe.selected_transition_batch(self.project, ['missing'])
        with self.assertRaises(ValueError): recipe.selected_transition_batch(self.project, ['0', '0'])
        with self.assertRaises(ValueError): recipe.selected_transition_batch(self.project, ['0', '4'])

    def test_no_cross_track_boundary_or_invalid_parameters(self):
        self.project.sequence.tracks.append(Track('other', 'video', [
            Clip('other-clip', AssetReference('image', 'synthetic.png'), Rational.of(1), Rational.of(2), Rational.of(0))]))
        with self.assertRaises(ValueError):
            recipe.selected_transition_batch(self.project, ['0', 'other-clip'])
        for duration in (0, -1, True, float('nan'), float('inf')):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                recipe.selected_transition_batch(self.project, ['0', '1'], duration=duration)
        with self.assertRaises(ValueError):
            recipe.selected_transition_batch(self.project, ['0', '1'], effect_id='')

    def test_32_boundary_limit_never_silently_splits(self):
        self.project.sequence.tracks[0].clips = [
            Clip(str(i), AssetReference('image', 'synthetic.png'), Rational.of(i), Rational.of(i+1), Rational.of(0))
            for i in range(34)]
        self.assertEqual(len(recipe.selected_transition_batch(self.project, [str(i) for i in range(33)])['commands']), 32)
        with self.assertRaisesRegex(ValueError, '32'):
            recipe.selected_transition_batch(self.project, [str(i) for i in range(34)])
