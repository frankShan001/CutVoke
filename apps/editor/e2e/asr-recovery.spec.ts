import { expect, test } from "@playwright/test";

test("ASR workbench discovers interrupted tasks and restores reviewed drafts without browser session state", async ({ page, request }) => {
  const id = `asr-recovery-${Date.now()}`;
  const name = `识别恢复 ${id}`;
  const created = await request.post("/api/v1/projects", { data: { projectId: id, name } });
  expect(created.status()).toBe(201);
  const rat = (value: number) => ({ num: String(value), den: "1" });
  const interrupted = {
    jobId: "asr-restart-ui", projectId: id, clipId: "voice", model: "small", requestedLanguage: "ja",
    status: "interrupted", phase: "等待恢复", progress: 35, error: "", result: null,
  };
  const done = {
    ...interrupted, status: "completed", phase: "识别完成", progress: 100,
    result: { clipId: "voice", sourceSignature: "fixture", model: "small", language: "ja",
      device: "cpu", duration: 1, segmentCount: 1,
      segments: [{ index: 1, text: "Original result", start: rat(0), end: rat(1), words: [] }] },
    drafts: [{ index: 1, text: "重启前已校对", start: "0", end: "1", words: [] }],
  };
  let current: object = interrupted;
  let resumes = 0;
  let saved: unknown;
  await page.route("**/api/v1/asr/status", (route) => route.fulfill({ json: {
    installed: true, ready: true, defaultModel: "base", models: ["tiny", "base", "small", "medium"],
    languages: ["auto", "zh", "en", "ja", "ko"], devicePreference: "cpu", devices: ["cpu"], message: "Ready",
  } }));
  await page.route(`**/api/v1/projects/${id}/asr-jobs`, (route) => route.fulfill({ json: { jobs: [current] } }));
  await page.route("**/api/v1/asr-jobs/asr-restart-ui", (route) => route.fulfill({ json: current }));
  await page.route("**/api/v1/asr-jobs/asr-restart-ui/resume", (route) => {
    resumes += 1;
    current = { ...interrupted, status: "queued", phase: "排队中", progress: 0 };
    return route.fulfill({ json: current });
  });
  await page.route("**/api/v1/asr-jobs/asr-restart-ui/drafts", (route) => {
    saved = route.request().postDataJSON().drafts;
    current = { ...done, drafts: saved, draftsUpdatedAt: route.request().postDataJSON().updatedAt };
    return route.fulfill({ json: current });
  });

  const open = async () => {
    await page.goto("/");
    await page.getByRole("button", { name: `打开工程 ${name}` }).click();
    await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "字幕" }).click();
  };
  await open();
  const panel = page.getByRole("region", { name: "自动字幕" });
  await expect(panel.getByText("等待恢复", { exact: false })).toBeVisible();
  await expect(panel.getByLabel("识别模型")).toHaveValue("small");
  await expect(panel.getByLabel("识别语言")).toHaveValue("ja");
  await panel.getByRole("button", { name: "继续识别", exact: true }).click();
  expect(resumes).toBe(1);
  await expect(panel.getByRole("button", { name: "继续识别", exact: true })).toHaveCount(0);

  // A new app session discovers saved server results without sessionStorage IDs.
  current = done;
  await page.evaluate(() => sessionStorage.clear());
  await open();
  await expect(panel.getByLabel("第 1 条识别文字")).toHaveValue("重启前已校对");
  await panel.getByLabel("第 1 条识别文字").fill("这次校对也保存");
  await expect.poll(() => saved).toEqual(expect.arrayContaining([
    expect.objectContaining({ text: "这次校对也保存" }),
  ]));
  await page.evaluate(() => sessionStorage.clear());
  await open();
  await expect(panel.getByLabel("第 1 条识别文字")).toHaveValue("这次校对也保存");
  const project = await (await request.get(`/api/v1/projects/${id}`)).json();
  expect(project.sequence.captions).toEqual([]);
});

test("ASR keeps unsent edits across immediate page close, reload and project changes", async ({ page, context, request }) => {
  const id = `asr-fast-leave-${Date.now()}`;
  const name = `快速离开 ${id}`;
  expect((await request.post("/api/v1/projects", { data: { projectId: id, name } })).status()).toBe(201);
  const otherName = `另一个工程 ${id}`;
  expect((await request.post("/api/v1/projects", {
    data: { projectId: `${id}-other`, name: otherName },
  })).status()).toBe(201);
  const rat = (value: number) => ({ num: String(value), den: "1" });
  let saved = [{ index: 1, text: "旧的服务端稿", start: "0", end: "1", words: [] }];
  let version = 1;
  let writes = 0;
  const done = () => ({ jobId: "asr-fast-leave", projectId: id, clipId: "voice", model: "tiny",
    requestedLanguage: "zh", status: "completed", phase: "识别完成", progress: 100, error: "",
    result: { clipId: "voice", sourceSignature: "fixture", model: "tiny", language: "zh", duration: 1,
      segmentCount: 1, segments: [{ index: 1, text: "Original", start: rat(0), end: rat(1), words: [] }] },
    drafts: saved, draftsUpdatedAt: version });
  await context.route("**/api/v1/asr/status", (route) => route.fulfill({ json: {
    installed: true, ready: true, models: ["tiny"], languages: ["zh"], message: "Ready",
  } }));
  await context.route(`**/api/v1/projects/${id}/asr-jobs`, (route) => route.fulfill({ json: { jobs: [done()] } }));
  await context.route("**/api/v1/asr-jobs/asr-fast-leave/drafts", (route) => {
    const input = route.request().postDataJSON();
    if (input.updatedAt >= version) {
      saved = input.drafts;
      version = input.updatedAt;
    }
    writes += 1;
    return route.fulfill({ json: done() });
  });
  const open = async (target: typeof page) => {
    await target.goto("/");
    await target.getByRole("button", { name: `打开工程 ${name}` }).click();
    await target.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "字幕" }).click();
    return target.getByRole("region", { name: "自动字幕" }).getByLabel("第 1 条识别文字");
  };
  const delayDebounce = async (target: typeof page) => target.evaluate(() => {
    const schedule = window.setTimeout.bind(window);
    window.setTimeout = ((...args: Parameters<typeof window.setTimeout>) => {
      if (args[1] === 350) args[1] = 60_000;
      return schedule(...args);
    }) as typeof window.setTimeout;
  });

  let field = await open(page);
  await expect(field).toHaveValue("旧的服务端稿");
  await expect.poll(() => writes).toBeGreaterThan(0);
  const firstWrites = writes;
  await delayDebounce(page);
  await field.fill("关闭页面前的新稿");
  await page.close();
  expect(writes).toBe(firstWrites);
  const reopened = await context.newPage();
  field = await open(reopened);
  await expect(field).toHaveValue("关闭页面前的新稿");
  await expect.poll(() => saved[0].text).toBe("关闭页面前的新稿");

  await delayDebounce(reopened);
  await field.fill("立刻刷新前的新稿");
  await reopened.reload();
  field = await open(reopened);
  await expect(field).toHaveValue("立刻刷新前的新稿");
  await expect.poll(() => saved[0].text).toBe("立刻刷新前的新稿");

  await delayDebounce(reopened);
  await field.fill("切换工程前的新稿");
  await reopened.goto("/");
  await reopened.getByRole("button", { name: `打开工程 ${otherName}` }).click();
  field = await open(reopened);
  await expect(field).toHaveValue("切换工程前的新稿");
  await expect.poll(() => saved[0].text).toBe("切换工程前的新稿");
});
