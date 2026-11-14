# Selected-boundary transitions / 选中边界批量转场

This small Python recipe builds an existing `edit.batch` payload. It adds no
backend command, GUI control, or automatic execution. Inspired by the concrete
[Shotcut user request](https://forum.shotcut.org/t/add-transition-to-all-clips-in-a-timeline/52179)
for selected intersections rather than every cut in a montage.

Load `recipe.py` with `importlib.util.spec_from_file_location`, then call
`selected_transition_batch(project_snapshot, selected_clip_ids)` using a fresh
`EditService.get_project(...)` snapshot. It returns `{'commands': [...]}`.

1. Select both sides of each wanted boundary. Only touching neighbors on the same
   video track qualify. Gaps, cross-track pairs and unselected cuts are untouched.
2. Existing transitions are skipped, not replaced. Locked eligible boundaries and
   stale selections fail before any command is sent.
3. Build a `Command(type='edit.batch', payload=payload, project_id=..., actor=...,
   expected_revision=snapshot.revision, command_id=...)`. Use the normal actor,
   lease and authorization flow; this helper grants no permissions.
4. Call `service.preview_command(command)`. Show eligible target clip IDs and
   `len(payload['commands'])` to the human. Review preview errors before commit.
5. Execute that same command only when the intended edit is authorized. A revision
   conflict requires a fresh snapshot/selection/preview, not blindly replacing the
   expected revision. Retry delivery with the same command ID for idempotence.
6. One batch is one undo point. The helper refuses **more than 32 boundaries**;
   it does not silently split. If the user chooses several smaller batches, each
   has its own revision and undo boundary, and later failure does not roll back
   earlier successful batches. Re-read and preview each batch.

This recipe does not change clip/source clocks or audio volume/fade parameters.
It does not add an audio-crossfade command. These structural guarantees do not
prove rendered sound is unchanged: inspect the actual transition with existing
preview/export tools. Timing, media handles and audible/visual quality need their
own media acceptance; the tests below are command-level, not a full render test.

## Verify

From the repository root with runtime dependencies and pytest installed:

```sh
python -m pytest -q tests/editing/test_selected_transition_recipe.py tests/editing/test_edit_batch.py
```

Tests cover selected targets, dry-run/commit count, untouched clocks/audio fields,
idempotent delivery, single undo, whole-batch rollback on a late invalid boundary,
gaps, existing transitions, locks, stale selection, cross-track exclusion and the
32-boundary limit. Synthetic path names are not opened; no personal media needed.

中文：仅在同一视频轨道上、左右两片都被选中且首尾相接时添加转场；跳过
已有转场，不覆盖。先 dry-run 展示目标及数量，再依照正常授权执行。
最多 32 个边界为一个原子批次；超过时拒绝，不自动拆分。人工选择分批后，
每批独立提交/撤销，后批失败不会撤销前批。此示例不调整音量/淡入淡出，
也不保证渲染听感不变；测试仅验证命令行为，不替代实际预览与音视频验收。
