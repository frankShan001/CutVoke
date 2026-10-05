import { expect, type APIRequestContext } from "@playwright/test";

export async function getBuiltinPackVersion(request: APIRequestContext): Promise<string> {
  const response = await request.get("/api/v1/resource-packs");
  expect(response.ok(), "built-in resource pack status should be available").toBeTruthy();
  const status = await response.json() as {
    active?: { packId?: string; version?: string };
  };
  expect(status.active?.packId).toBe("cutvoke.builtin-resources");
  if (!status.active?.version) throw new Error("built-in resource pack version is missing");
  return status.active.version;
}
