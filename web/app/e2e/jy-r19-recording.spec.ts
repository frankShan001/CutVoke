import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { getBuiltinPackVersion } from "./resourcePackHelpers";

test.use({ video: "on" });

type Project = {
  projectId: string;
  revision: string;
  sequence: {
    width: number;
    height: number;
    fps: { num: string; den: string };
    tracks: Array<{
      id: string;
      kind: string;
      role?: string;
      visible?: boolean;
      muted?: boolean;
      locked?: boolean;
      clips: Array<{
        id: string;
        assetRef: { sourcePath: string; assetId?: string };
        role?: "sticker";
        timelineStart: { num: string; den: string };
        timelineEnd: { num: string; den: string };
        sourceStart?: { num: string; den: string };
        speed?: { num: string; den: string };
        attachedToClipId?: string | null;
        keyframes?: Record<string, Array<{
          id: string; time: { num: string; den: string }; value: unknown; interpolation?: string;
        }>>;
        effects: Array<{
          effectId: string; version?: string; params?: Record<string, unknown>;
          presetId?: string; range?: { start: { num: string; den: string }; end: { num: string; den: string } };
        }>;
      }>;
    }>;
    captions: Array<{
      id: string; text: string; start: { num: string; den: string }; end: { num: string; den: string };
      [key: string]: unknown;
    }>;
  };
};

const apiPath = (id: string) => `/api/v1/projects/${encodeURIComponent(id)}`;
const outputDir = resolve(process.cwd(), process.env.CUTVOKE_JY_R19_OUTPUT
  ?? "../../output/acceptance/jy-r19-20260925");
const cameraSourceNames = [
  "street-traffic.webm",
  "waves-of-the-sea.webm",
  "rosie-the-therapy-dog.webm",
  "small-waterfall.webm",
  "rain-in-kenwood.webm",
  "steam-train-at-station.webm",
];
const bgmPath = process.env.CUTVOKE_JY_CAMERA_BGM
  ? resolve(process.env.CUTVOKE_JY_CAMERA_BGM)
  : resolve(process.cwd(), "../../src/cutvoke/assets/audio/tech_minimal.wav");
const bgmFileName = bgmPath.split(/[\\/]/).pop() ?? "tech_minimal.wav";
const titleText = "六段旅程 · 一次剪成";
const captionText = "从第一帧开始，把故事剪出来";

async function readProject(request: APIRequestContext, id: string): Promise<Project> {
  const response = await request.get(apiPath(id));
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

async function apply(
  request: APIRequestContext,
  id: string,
  type: string,
  payload: object,
): Promise<Project> {
  const current = await readProject(request, id);
  const response = await request.post(`${apiPath(id)}/commands`, {
    data: {
      type,
      payload,
      expectedRevision: current.revision,
      commandId: `jy-r19-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      actor: { kind: "human", id: "jy-r19-web-recording" },
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return readProject(request, id);
}

async function createSixClipProject(request: APIRequestContext): Promise<{ id: string; name: string }> {
  const cameraMediaDir = process.env.CUTVOKE_JY_CAMERA_MEDIA_DIR;
  let sources: string[];
  if (cameraMediaDir) {
    sources = cameraSourceNames.map((filename) => resolve(cameraMediaDir, filename));
    for (const source of sources) expect(existsSync(source), `missing source ${source}`).toBe(true);
    expect(new Set(sources).size).toBe(6);
  } else {
    const fixtureResponse = await request.get(apiPath("e2e-fixture"));
    expect(fixtureResponse.ok(), await fixtureResponse.text()).toBeTruthy();
    const fixture = await fixtureResponse.json() as Project;
    sources = fixture.sequence.tracks
      .filter((track) => track.kind === "video")
      .flatMap((track) => track.clips.map((clip) => clip.assetRef.sourcePath));
  }
  expect(sources.length).toBeGreaterThanOrEqual(3);

  const id = `jy-r19-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const realFootage = Boolean(cameraMediaDir);
  const width = Number(process.env.CUTVOKE_JY_CAMERA_WIDTH ?? 640);
  const height = Number(process.env.CUTVOKE_JY_CAMERA_HEIGHT ?? 360);
  const fps = Number(process.env.CUTVOKE_JY_CAMERA_FPS ?? 15);
  const name = `${realFootage ? "实拍" : "合成"}六段样片 ${id.slice(-5)}`;
  const created = await request.post("/api/v1/projects", {
    data: { projectId: id, name, width, height, fps },
  });
  expect(created.status(), await created.text()).toBe(201);
  await apply(request, id, "track.add", { trackId: "v1", kind: "video" });
  for (let index = 0; index < 6; index += 1) {
    await apply(request, id, "clip.insert", {
      trackId: "v1",
      clipId: `shot-${index + 1}`,
      sourcePath: sources[realFootage ? index : index % 3],
      timelineStart: { num: index * 2, den: 1 },
      timelineEnd: { num: (index + 1) * 2, den: 1 },
    });
  }

  await apply(request, id, "track.add", { trackId: "a1", kind: "audio" });
  await apply(request, id, "clip.insert", {
    trackId: "a1",
    clipId: "bgm-one",
    sourcePath: bgmPath,
    timelineStart: { num: 0, den: 1 },
    timelineEnd: { num: 12, den: 1 },
  });
  return { id, name };
}

async function openProject(page: Page, name: string): Promise<void> {
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  await expect(page.getByRole("heading", { name: "时间线" })).toBeVisible();
  await expect(page.locator(".clip-block")).toHaveCount(7); // 六段视频与独立音乐
}

function structuralSignature(project: Project): string {
  const shape = {
    width: project.sequence.width,
    height: project.sequence.height,
    fps: project.sequence.fps,
    tracks: project.sequence.tracks.map((track) => ({
      id: track.id,
      kind: track.kind,
      role: track.role ?? "",
      visible: track.visible ?? true,
      muted: track.muted ?? false,
      locked: track.locked ?? false,
      clips: track.clips.map((clip) => ({
        id: clip.id,
        assetRef: clip.assetRef,
        role: clip.role ?? "",
        timelineStart: clip.timelineStart,
        timelineEnd: clip.timelineEnd,
        sourceStart: clip.sourceStart,
        speed: clip.speed,
        attachedToClipId: clip.attachedToClipId ?? null,
        keyframes: clip.keyframes ?? {},
        effects: clip.effects,
      })),
    })),
    captions: project.sequence.captions,
  };
  const canonicalize = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(canonicalize);
    if (typeof value === "object" && value !== null) {
      return Object.fromEntries(Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, canonicalize(item)]));
    }
    return value;
  };
  return JSON.stringify(canonicalize(shape));
}

async function createAgentReplica(request: APIRequestContext, source: Project): Promise<Project> {
  const id = `jy-r18-agent-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const created = await request.post("/api/v1/projects", {
    data: {
      projectId: id, name: `Agent 等价工程 ${id.slice(-5)}`,
      width: source.sequence.width, height: source.sequence.height,
      fps: Number(source.sequence.fps.num) / Number(source.sequence.fps.den),
    },
  });
  expect(created.status(), await created.text()).toBe(201);

  const leaseResponse = await request.post(`${apiPath(id)}/edit-lock`, {
    data: { owner: "jy-r18-equivalence-agent", ttlSeconds: 120 },
  });
  expect(leaseResponse.ok(), await leaseResponse.text()).toBeTruthy();
  const lease = await leaseResponse.json() as { lease: { leaseId: string } };
  const leaseId = lease.lease.leaseId;
  let serial = 0;
  const applyAgent = async (type: string, payload: object) => {
    const current = await readProject(request, id);
    const response = await request.post(`${apiPath(id)}/commands`, {
      data: {
        type, payload, expectedRevision: current.revision, editLeaseId: leaseId,
        commandId: `jy-r18-agent-${++serial}`,
        actor: { kind: "agent", id: "jy-r18-equivalence-agent" },
      },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
  };

  try {
    for (const track of source.sequence.tracks) {
      await applyAgent("track.add", {
        trackId: track.id, kind: track.kind, role: track.role ?? "",
      });
    }
    for (const track of source.sequence.tracks) {
      for (const clip of track.clips) {
        const textEffect = clip.effects.find((effect) => effect.effectId === "cutvoke.text");
        const payload: Record<string, unknown> = {
          trackId: track.id,
          clipId: clip.id,
          timelineStart: clip.timelineStart,
          timelineEnd: clip.timelineEnd,
          sourceStart: clip.sourceStart,
          attachedToClipId: clip.attachedToClipId ?? undefined,
        };
        if (clip.role) payload.role = clip.role;
        if (clip.assetRef.sourcePath) payload.sourcePath = clip.assetRef.sourcePath;
        if (clip.assetRef.assetId) payload.assetId = clip.assetRef.assetId;
        if (clip.assetRef.resourceRef) payload.resourceRef = clip.assetRef.resourceRef;
        if (textEffect) {
          payload.text = textEffect.params;
          if (textEffect.presetId) payload.textPresetId = textEffect.presetId;
        }
        await applyAgent("clip.insert", payload);

        for (const [param, keyframes] of Object.entries(clip.keyframes ?? {})) {
          for (const keyframe of keyframes) {
            await applyAgent("clip.keyframe", {
              clipId: clip.id, param, action: "add", keyframeId: keyframe.id,
              time: keyframe.time, value: keyframe.value,
              interpolation: keyframe.interpolation ?? "linear",
            });
          }
        }

        for (const [effectIndex, effect] of clip.effects.entries()) {
          if (effect.effectId === "cutvoke.text") continue;
          const params = effect.params ?? {};
          if (effect.presetId) {
            await applyAgent("builtinPreset.apply", {
              clipId: clip.id, presetId: effect.presetId,
            });
          } else if (effect.effectId.startsWith("cutvoke.transition.")) {
            await applyAgent("effect.setTransition", {
              clipId: clip.id, effectId: effect.effectId, params,
            });
          } else if (effect.effectId.startsWith("cutvoke.anim.")) {
            await applyAgent("effect.setAnimation", {
              clipId: clip.id, effectId: effect.effectId, params,
            });
          } else if (!(track.role === "sticker" && effect.effectId === "cutvoke.transform"
              && effectIndex === 0)) {
            await applyAgent("effect.add", {
              clipId: clip.id, effectId: effect.effectId,
              version: effect.version ?? "1.0.0", params,
            });
            if (effect.range) {
              await applyAgent("effect.update", {
                clipId: clip.id, effectId: effect.effectId, effectIndex,
                range: effect.range,
              });
            }
          }
        }
      }
    }
    for (const caption of source.sequence.captions) {
      const { id: captionId, text, start, end, ...style } = caption;
      await applyAgent("caption.add", { captionId, text, start, end, ...style });
    }
  } finally {
    const released = await request.post(`${apiPath(id)}/edit-lock/release`, {
      data: { leaseId },
    });
    expect(released.ok(), await released.text()).toBeTruthy();
  }
  return readProject(request, id);
}

test("JY-R19 records a six-clip editing, save, preview, export, and package workflow", async ({ page, request }) => {
  test.setTimeout(240_000);
  mkdirSync(outputDir, { recursive: true });
  const { id, name } = await createSixClipProject(request);

  await openProject(page, name);
  const creativeTabs = page.getByRole("tablist", { name: "创作域" });
  const editorTabs = page.getByRole("tablist", { name: "工程编辑面板" });

  // Insert an editable title through the text preset library.
  await creativeTabs.getByRole("tab", { name: "文字" }).click();
  const titleLibrary = page.getByRole("region", { name: "文字标题库" });
  await expect(titleLibrary).toBeVisible();
  await titleLibrary.getByLabel("标题内容").fill(titleText);
  const titleCard = titleLibrary.locator(".title-library__card").first();
  await expect(titleCard).toBeVisible();
  await titleCard.getByRole("button", { name: /添加标题/ }).click();
  await expect(page.getByText(/已添加可编辑标题/)).toBeVisible();

  // Add a separate caption track entry from the caption workbench.
  const captionTab = creativeTabs.getByRole("tab", { name: "字幕" });
  await captionTab.click();
  const captionWorkbench = page.getByRole("tabpanel", { name: "字幕" });
  await captionWorkbench.getByLabel("字幕文本").fill(captionText);
  await captionWorkbench.getByLabel("开始 (s)").fill("0.25");
  await captionWorkbench.getByLabel("结束 (s)").fill("1.8");
  await captionWorkbench.getByRole("button", { name: "添加字幕" }).click();
  await expect.poll(async () => (await readProject(request, id)).sequence.captions
    .some((caption) => caption.text === captionText)).toBe(true);

  // Add an approved transparent sticker on its own overlay track.
  await creativeTabs.getByRole("tab", { name: "贴纸" }).click();
  const stickerTab = page.getByRole("region", { name: "贴纸库" });
  await stickerTab.getByLabel("贴纸分类").selectOption("指引");
  const generatedStickerCard = stickerTab.locator(".sticker-card")
    .filter({ hasText: "向下箭头" });
  await expect(generatedStickerCard).toHaveCount(1);
  const generatedProvenance = generatedStickerCard.locator(".sticker-card__provenance");
  await expect(generatedProvenance.locator("summary")).toHaveText("来源信息");
  await generatedProvenance.locator("summary").click();
  await expect(generatedProvenance).toContainText("basic-navigation-sticker-atlas-20260925.png");
  await expect(generatedProvenance).toContainText("legacy-icon-atlases-20260925.extraction.json");
  await stickerTab.getByLabel("贴纸分类").selectOption("花草");
  const stickerCard = stickerTab.locator(".sticker-card").filter({ hasText: "樱花枝" });
  await expect(stickerCard).toHaveCount(1);
  const provenance = stickerCard.locator(".sticker-card__provenance");
  await expect(provenance.locator("summary")).toHaveText("来源信息");
  await provenance.locator("summary").click();
  await expect(provenance).toContainText("botanical-sticker-sheet-20260924.extraction.json");
  await stickerCard.getByRole("button", { name: "添加贴纸 樱花枝" }).click();
  await expect(page.getByText("已添加贴纸「樱花枝」到独立叠加轨")).toBeVisible();
  await expect.poll(async () => {
    const project = await readProject(request, id);
    const stickerTrack = project.sequence.tracks.find((track) => track.role === "sticker");
    const clip = stickerTrack?.clips[0];
    return clip?.assetRef.resourceRef;
  }).toMatchObject({
    resourceId: "cutvoke.sticker.botanical_cherry_blossom",
    packId: "cutvoke.builtin-resources",
    packVersion: await getBuiltinPackVersion(request),
    resourceVersion: "1.0.0",
    sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
  });

  // Apply a bundled style-filter preset from the resource library.
  const videoClips = page.locator('.timeline-track__lane[data-track-id="v1"] .clip-block');
  await videoClips.nth(0).click();
  await creativeTabs.getByRole("tab", { name: "滤镜" }).click();
  const presetLibrary = page.getByRole("region", { name: "内置预设库" });
  const presetTypes = presetLibrary.getByRole("group", { name: "预设类型" });
  await presetTypes.getByRole("button", { name: /滤镜/ }).click();
  const presetCard = presetLibrary.locator(".resource-preset-card").first();
  await expect(presetCard).toBeVisible();
  const filterPresetName = (await presetCard.locator(".resource-card__name").innerText()).trim();
  await presetCard.getByRole("button", { name: "应用" }).click();
  await expect.poll(async () => {
    const project = await readProject(request, id);
    const clip = project.sequence.tracks.find((track) => track.id === "v1")!.clips[0];
    return clip.effects.some((effect) => effect.effectId.startsWith("cutvoke.fx."));
  }).toBe(true);

  await videoClips.nth(1).click();
  await editorTabs.getByRole("tab", { name: "片段属性" }).click();
  const entrance = page.getByLabel("入场动画");
  await expect(entrance).toBeVisible();
  await entrance.selectOption("cutvoke.anim.rotateIn");
  await expect.poll(async () => {
    const project = await readProject(request, id);
    const clip = project.sequence.tracks.find((track) => track.id === "v1")!.clips[1];
    return clip.effects.some((effect) => effect.effectId.startsWith("cutvoke.anim."));
  }).toBe(true);

  // Use the seam affordance to target the incoming shot, preview, and apply a transition.
  await page.getByRole("button", { name: "在 2.00 秒的片段接缝添加转场" }).click();
  await expect(editorTabs.getByRole("tab", { name: "转场" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByLabel("后一段片段（转场挂此段）")).toHaveValue("shot-2");
  await page.getByRole("button", { name: "预览当前转场" }).click();
  const transitionPreview = page.locator('.resource-preview img[alt="当前转场与时长在两段片段之间的真实预览帧"]');
  await expect(transitionPreview).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "应用转场" }).click();
  await expect.poll(async () => {
    const project = await readProject(request, id);
    const incoming = project.sequence.tracks.find((track) => track.id === "v1")!.clips[1];
    return incoming.effects.some((effect) => effect.effectId.startsWith("cutvoke.transition."));
  }).toBe(true);

  // Save, then capture the workbench state and independent preview frames.
  // The editor commits each action as an authoritative project command; reloading below verifies persistence.
  await expect.poll(async () => (await readProject(request, id)).sequence.tracks
    .find((track) => track.id === "v1")!.clips.length).toBe(6);
  const playerVideo = page.locator(".player__video");
  await expect(playerVideo).toBeVisible();
  await expect.poll(async () => playerVideo.evaluate((element) => {
    const video = element as HTMLVideoElement;
    return video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA && !video.seeking;
  }), { timeout: 30_000 }).toBe(true);
  const samplePlayerBrightness = async () => playerVideo.evaluate((element) => {
    const video = element as HTMLVideoElement;
    const canvas = document.createElement("canvas");
    canvas.width = 32;
    canvas.height = 18;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || video.seeking) return 0;
    try {
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      let brightness = 0;
      for (let index = 0; index < pixels.length; index += 4) {
        brightness += (pixels[index] + pixels[index + 1] + pixels[index + 2]) / 3;
      }
      return brightness / (canvas.width * canvas.height);
    } catch {
      return 0;
    }
  });
  await expect.poll(samplePlayerBrightness, { timeout: 30_000 }).toBeGreaterThan(12);
  const playerPreview = {
    timeLabel: (await page.locator(".player-controls__time").innerText()).trim(),
    meanBrightnessRgb: Number((await samplePlayerBrightness()).toFixed(2)),
    readyState: await playerVideo.evaluate((element) => (element as HTMLVideoElement).readyState),
  };
  await page.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  }));
  await page.screenshot({ path: resolve(outputDir, "operation-result.png"), fullPage: false });

  const project = await readProject(request, id);
  writeFileSync(resolve(outputDir, "six-clip-project.json"), JSON.stringify(project, null, 2), "utf8");
  const previewFrames = [1.75, 2.25, 3.25];
  for (const time of previewFrames) {
    const response = await request.get(`${apiPath(id)}/preview-frame?t=${time}&size=640x360`);
    expect(response.ok(), `preview at ${time}s: ${await response.text()}`).toBeTruthy();
    const bytes = await response.body();
    expect(bytes.subarray(0, 8)).toEqual(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
    writeFileSync(resolve(outputDir, `preview-${time.toFixed(2)}s.png`), bytes);
  }

  // Export the final movie, a cover, and an editable handoff package from the Web dialog.
  await page.getByRole("button", { name: "导出" }).click();
  const exportDialog = page.getByRole("dialog", { name: "导出成片" });
  await expect(exportDialog).toBeVisible();
  const moviePath = resolve(outputDir, "cutvoke-six-clip-sample.mp4");
  const coverPath = resolve(outputDir, "cutvoke-six-clip-cover.png");
  const packPath = resolve(outputDir, "cutvoke-six-clip-sample.cutvokepack.zip");
  await exportDialog.getByLabel("输出文件路径").fill(moviePath);
  await exportDialog.getByRole("button", { name: "开始导出" }).click();
  await expect(exportDialog.getByText("导出完成")).toBeVisible({ timeout: 180_000 });
  await exportDialog.getByLabel("封面时间点").fill("3.25");
  await exportDialog.getByLabel("封面输出路径").fill(coverPath);
  await exportDialog.getByRole("button", { name: "导出封面" }).click();
  await expect(exportDialog.getByText("封面导出完成")).toBeVisible({ timeout: 30_000 });
  await exportDialog.getByLabel("资源包输出路径").fill(packPath);
  await exportDialog.getByRole("button", { name: "打包资源包" }).click();
  await expect(exportDialog.getByText("打包完成")).toBeVisible({ timeout: 60_000 });

  await page.keyboard.press("Escape");
  await expect(exportDialog).toHaveCount(0);
  await page.reload();
  const reopen = page.getByRole("button", { name: `打开工程 ${name}` });
  if (await reopen.isVisible().catch(() => false)) await reopen.click();
  await expect(page.getByRole("heading", { name: "时间线" })).toBeVisible();
  const reopened = await readProject(request, id);
  const videoTrack = reopened.sequence.tracks.find((track) => track.id === "v1")!;
  expect(videoTrack.clips).toHaveLength(6);
  expect(videoTrack.clips[0].effects.some((effect) => effect.effectId.startsWith("cutvoke.fx."))).toBe(true);
  expect(videoTrack.clips[1].effects.some((effect) => effect.effectId.startsWith("cutvoke.anim."))).toBe(true);
  expect(videoTrack.clips[1].effects.some((effect) => effect.effectId.startsWith("cutvoke.transition."))).toBe(true);
  expect(reopened.sequence.captions.some((caption) => caption.text === captionText)).toBe(true);
  expect(reopened.sequence.tracks.some((track) => track.kind === "audio"
    && track.clips.some((clip) => clip.assetRef.sourcePath.endsWith(bgmFileName)))).toBe(true);
  expect(reopened.sequence.tracks.some((track) => track.kind === "text" && track.clips.length === 1)).toBe(true);
  expect(reopened.sequence.tracks.some((track) => track.role === "sticker"
    && track.clips.some((clip) => clip.assetRef.sourcePath.includes("botanical_cherry_blossom.png")))).toBe(true);
  const agentReplica = await createAgentReplica(request, reopened);
  expect(structuralSignature(agentReplica)).toBe(structuralSignature(reopened));
  writeFileSync(resolve(outputDir, "acceptance-report.json"), JSON.stringify({
    projectId: id,
    projectName: name,
    revision: reopened.revision,
    dimensions: `${process.env.CUTVOKE_JY_CAMERA_WIDTH ?? 640}x${process.env.CUTVOKE_JY_CAMERA_HEIGHT ?? 360}`,
    fps: `${process.env.CUTVOKE_JY_CAMERA_FPS ?? 15}/1`,
    durationSeconds: 12,
    videoClipCount: videoTrack.clips.length,
    audioClipCount: reopened.sequence.tracks.filter((track) => track.kind === "audio")
      .reduce((sum, track) => sum + track.clips.length, 0),
    captionCount: reopened.sequence.captions.length,
    stickerTracks: reopened.sequence.tracks.filter((track) => track.role === "sticker").length,
    agentReplica: {
      projectId: agentReplica.projectId,
      revision: agentReplica.revision,
      structuralParity: true,
      construction: "Rebuilt from the Web-saved project structure through leased Agent commands.",
    },
    filterPresetName,
    playerPreview,
    videoEffects: videoTrack.clips.map((clip) => ({ id: clip.id, effects: clip.effects.map((effect) => effect.effectId) })),
    outputs: {
      movie: moviePath,
      cover: coverPath,
      project: resolve(outputDir, "six-clip-project.json"),
      package: packPath,
      previewFrames: previewFrames.map((time) => resolve(outputDir, `preview-${time.toFixed(2)}s.png`)),
      screenshot: resolve(outputDir, "operation-result.png"),
    },
    mediaMode: process.env.CUTVOKE_JY_CAMERA_MEDIA_DIR ? "licensed real-camera footage" : "synthetic footage",
    sourceManifest: process.env.CUTVOKE_JY_CAMERA_MANIFEST
      ? resolve(process.env.CUTVOKE_JY_CAMERA_MANIFEST) : undefined,
    assessment: process.env.CUTVOKE_JY_CAMERA_MEDIA_DIR
      ? "Local-only workflow with original Commons source files retained. Attribution is recorded in the adjacent source manifest; this does not establish release rights beyond each source license or independent user acceptance."
      : "Synthetic browser workflow evidence. Not a substitute for complex real-footage quality review or release approval.",
  }, null, 2), "utf8");

  const recording = page.video();
  await page.close();
  await recording?.saveAs(resolve(outputDir, "operation-recording.webm"));
});
