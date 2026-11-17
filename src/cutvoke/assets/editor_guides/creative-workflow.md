# Executable creative workflow / 可执行创作闭环

Use `creative_workflow` alongside the existing principles, project-plan and review guides. It supplies structured recipes and checks external Agent artifacts. It does not choose usable footage, verify facts/rights, watch media or judge artistic success. Nothing in this tool writes the project or saves files. Keep its JSON artifacts under the established projectRoot, for example `notes/plan-r1.json` and `review/r1/`.

本入口提供可检索的风格配方与计划、审片、局修约定，不代替选材、事实核验或视听判断，不自动写工程或保存文件。用途、风格、发布条件分开选择；技术有效不等于精彩。

## Discover a relevant recipe / 按需发现

```json
{"operation":"list","query":"教程","locale":"zh-CN","limit":3}
```

Use `query` (English or Chinese title/topic), `purpose`, `style`, `offset`, `limit` (1..10, default 3). The response includes purpose/style IDs, total and nextOffset, without recipe bodies. Search is substring matching, not semantic footage search. Filters combine. Fetch one result with its returned request:

```json
{"operation":"recipe","recipeId":"task-tutorial","locale":"zh-CN"}
```

Eighteen recipes cover product demonstration/micro-story/brand concept; faithful conversation and self-contained highlights; knowledge explanation; reproducible tutorials; personal travel/atmosphere/route guides; factual news/history; observational portraits; fiction; performance/concept/story MV; events; family/wedding memories. `highlights` also routes by the content's actual audience task. Animation is a medium, cinematic is an underspecified aesthetic, and portrait/social is delivery, not a purpose. A style is an option with prerequisites, not a preset effects stack or a universal duration/cut-rate rule.

每张配方含目标、素材前提、结构、镜头节奏、声音、字幕、常见失败、验收和缺素材替代方案；附报告来源与读取限制。检查现有素材能否支持，缺关键证据先补录、降低主张或换方案，不能靠装饰填逻辑空白。

## Plan → rough cut → evidence / 计划到证据

1. Retrieve only the needed contract with `{"operation":"schema","kind":"plan"}` (also `review`, `repair`). Schemas are strict, versioned JSON Schema subsets. Times are exact rational seconds, end exclusive. Timeline ranges are local to the named active sequence; sourceRange is in the original asset's clock. No implicit time mapping is performed.
2. Bind every document to `projectId`, `revision`, `sequenceId`. Plan fields distinguish brief purpose, delivery dimensions/context, usable asset selections with source ranges/provenance/rights notes, protected timeline ranges, beats linking stable clip IDs and selection IDs, sound/caption intention, gaps, exceptions and required review evidence. Repeated assets may have multiple selection IDs and clip instances. Do not guess IDs from order or filename.
3. `{"operation":"validate","document":{...}}` checks the schema and current project references. It checks declared source bounds when registered duration is available, but does not certify usable content or map reverse, speed curves, freeze frames and nested/shared instances. It does not persist the plan or enforce its semantics on unrelated editing commands. A plan here describes a current assembled rough cut; an earlier preassembly outline may remain free-form until its actual clip IDs exist.
4. Use existing help and commands to assemble. Acquire edit_lock; preview supported operations; submit with expectedRevision, lease and stable commandId. Use edit.batch (up to 32) and normal undo. Reconcile conflicts, do not merely replace the revision. Release the lease in finally, including before long exports or handoff.
5. Use actual preview_frames (max 6) and continuous output. preview_prepare prepares the entire sequence and has no range argument; a bounded dynamic prototype uses export.enqueue's range. Poll job IDs with bounded timeouts. A low-resource device can review representative ranges serially before a full export; do not silently reduce final delivery quality.

## Review → local repair → recheck / 审片到局修

A review contains evidence IDs, kind, time range, artifact reference, inspection declaration, inspector and observation; issues contain time range, severity, evidence IDs, suggested action, preserve statements and status. Evidence types are technical, frames, cut-neighborhood, continuous-av, mobile-readability and meaning-source-check. Record pending sensory evidence honestly. Artifact references are Agent-owned references: the tool does not open paths/URLs, decode, hash or establish their authenticity. A declared inspection remains the caller's declaration. `preview_frames` and `preview_compare` return `sha256` and `sizeBytes` for each nonempty frame, calculated from the exact PNG bytes returned to the caller (including cache hits). Record them with the response revision, requested sample time and comparison side when retaining evidence. These are byte-integrity checks, not perceptual hashes, proof of inspection or durable storage: preview-cache paths can be evicted; keep authorized evidence copies separately. Empty frames have no PNG digest.

```json
{"operation":"validate","document":{"kind":"review","...":"use the returned schema"}}
```

The JSON above is schematic, not a valid complete document. Use the returned schema and runnable example for exact data. A valid review can have pending evidence or unresolved issues; it never means the movie is accepted.

A repair identifies open issue IDs, a bounded scope, exact existing command payloads, clips to preserve byte-for-byte, rationale, context recheck ranges and review requirements. Supply the current plan and review as context:

```json
{"operation":"preview-repair","document":{"...":"complete repair"},"plan":{"...":"complete plan"},"review":{"...":"complete review"}}
```

This uses the existing edit.batch dry run. It rejects stale/mismatched project/revision/sequence, missing or uninspected evidence, unresolved ID/range references, nested/non-batchable or invalid commands, locked-track edits, changes beyond scope, changes overlapping protected ranges, and changes to preserveClipIds. Clip edits in projects with propertyLinks, track mattes, master/track/bus audio processing or sidechains are conservatively routed to ordinary dependency-aware review; nested/shared composition edits are also rejected here until their instance impacts can be fully mapped. Changes must be bounded clip/caption entities; settings, track and global changes require the ordinary explicit editing workflow. The check conservatively uses each changed entity's full before/after range: a partial edit of a long clip may need a wider reviewed scope. Protection is conservative across tracks, not a proof of semantic meaning or source retention.

When no checks fail, `nextCall` supplies the exact batch and revision for command_apply. Acquire a lease and provide a stable commandId. The preview grants no new authorization and commits nothing. `readyForCommandApply` means this bounded technical preview passed, not that preservation of meaning, facts, emotion or artistic quality was verified. Never auto-retry a conflict with a new revision. The ordinary command API does not automatically enforce these optional plan artifacts.

修复前的问题必须有当前版本的实际证据。先修事实、原意、缺步骤和不可懂声音，再考虑装饰。保护声明中的语义仍需对照原素材和视听判断，不能让工具伪装完成。

Any commit or undo creates a new revision; old review evidence is stale. Rebuild plan/review from the new snapshot, produce new output, inspect recheckRanges including neighboring cuts, then the full continuous AV and final exported bytes. Do not relabel an old render with the new revision. Report who actually watched/listened, which tests were automated, and what remains unassessed. Audience comprehension or blind comparison is a separate evaluation, not an automatic score returned here.

## Runnable integration / 可运行示例

`skills/cutvoke-editor/scripts/creative_demo.py --output <new-absolute-directory>` creates its own color/tone fixtures and uses real stdio MCP: search → recipe/schema → rough cut → plan/review validation → local repair dry run → explicit apply/idempotent retry → new version evidence → export. It leaves JSON artifacts, calls, actual frames and MP4s. It verifies deterministic caption preservation, pixels, audio, duration and decoding. It is a reproducible protocol example, not a real product film, meaningful speech review or proof of improved creative quality. No paid provider, model download or personal media is used.
