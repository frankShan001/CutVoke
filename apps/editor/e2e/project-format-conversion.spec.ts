import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, readdirSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

// Actual old-format fixtures and HTTP backend; no route mocks. The Python
// helpers used here are the same fixtures checked by storage migration tests.
function fixture(kind: "directory" | "package") {
  const root = mkdtempSync(join(tmpdir(), "cutvoke-browser-format-"));
  const script = [
    "import json,runpy,sys", "from pathlib import Path", "root=Path(sys.argv[1])",
    kind === "directory" ? [
      "helper=runpy.run_path('tests/storage/test_project_format_migration.py')",
      "source=root/'legacy';source.mkdir()", "helper['make_legacy'](source/'project.sqlite')",
      "(source/'project.cutvoke').write_text(json.dumps({'format':'cutvoke.project-directory','version':1,'database':'project.sqlite','projectId':'p','rootPath':'/old/location'}))",
    ].join("\n") : [
      "helper=runpy.run_path('tests/storage/test_project_package_conversion.py')",
      "source,_=helper['old_package'](root,media=True)",
    ].join("\n"), "print(source)",
  ].join("\n");
  const path = execFileSync("python", ["-c", script, root], { cwd: resolve("../.."), encoding: "utf8" }).trim();
  return { path, root };
}
function fingerprint(path: string): Record<string, string> {
  if (!statSync(path).isDirectory()) return { package: createHash("sha256").update(readFileSync(path)).digest("hex") };
  return Object.fromEntries(readdirSync(path).sort().map(name => [name,
    createHash("sha256").update(readFileSync(join(path, name))).digest("hex")]));
}
for (const kind of ["directory", "package"] as const) {
  test(`legacy ${kind}: Close leaves source and catalogue unchanged; explicit conversion opens a new copy`, async ({ page, request }) => {
    test.setTimeout(90_000);
    const { path } = fixture(kind);
    const original = fingerprint(path);
    const ids = async () => {
      const response = await request.get("/api/v1/projects");
      expect(response.ok()).toBeTruthy();
      return ((await response.json()).projects as { id: string }[]).map(p => p.id).sort();
    };
    const before = await ids();
    await page.goto("/");
    const pathBox = page.getByRole("textbox", { name: kind === "directory" ? "工程目录路径" : "资源包路径", exact: true });
    const open = page.getByRole("button", { name: kind === "directory" ? "打开工程目录" : "解包打开", exact: true });
    await pathBox.fill(path);
    await open.click();
    const dialog = page.getByRole("dialog", { name: "旧工程需要转换" });
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("原工程保留不变");
    await expect(dialog.getByRole("button", { name: "关闭", exact: true })).toBeFocused();
    await dialog.getByRole("button", { name: "关闭", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    expect(await ids()).toEqual(before);
    expect(fingerprint(path)).toEqual(original);
    await expect(page.getByRole("heading", { name: "时间线", exact: true })).toHaveCount(0);

    await open.click();
    await expect(dialog).toBeVisible();
    const endpoint = kind === "directory" ? "/project-directories/convert" : "/packages/format/convert";
    const converted = page.waitForResponse(response => response.url().endsWith(endpoint) && response.request().method() === "POST");
    await dialog.getByRole("button", { name: "转换副本并打开", exact: true }).click();
    const response = await converted;
    const result = await response.json();
    expect(response.ok(), JSON.stringify(result)).toBeTruthy();
    expect(result.projectId).not.toBe("p");
    await expect(page.getByRole("heading", { name: "时间线", exact: true })).toBeVisible();
    await expect(dialog).toHaveCount(0);
    expect(await ids()).toEqual([...before, result.projectId].sort());
    expect(fingerprint(path)).toEqual(original);
    const project = await request.get(`/api/v1/projects/${encodeURIComponent(result.projectId)}`);
    expect(project.ok()).toBeTruthy();
    expect((await project.json()).schemaVersion).toBe("2");
    await page.screenshot({ path: test.info().outputPath(`${kind}-converted.png`) });
  });
}
