import { expect, type APIRequestContext } from '@playwright/test';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, realpathSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { structuralSignature, type Project } from './projectEquivalence';
import { isAbsolute, relative, resolve } from 'node:path';

/** Keep synthetic exports inside the same boundary as production projects. */
export async function projectOutputPath(request: APIRequestContext, id: string, filename: string): Promise<string> {
  const response = await request.get(`/api/v1/projects/${encodeURIComponent(id)}/directory`);
  expect(response.ok(), await response.text()).toBeTruthy();
  const directory = await response.json();
  expect(directory.storageKind).toBe('directory');
  expect(directory.exportDirectory).toBeTruthy();
  return resolve(directory.exportDirectory, filename);
}

/** Copied media has a hash basename; assert the actual source bytes. */
export function sameMediaContent(source: string, expected: string): boolean {
  return Boolean(source && existsSync(source) && readFileSync(source).equals(readFileSync(expected)));
}

export function hasBuiltinSticker(clip: { assetRef: { sourcePath: string } }, filename: string): boolean {
  return sameMediaContent(clip.assetRef.sourcePath,
    fileURLToPath(new URL(`../../../src/cutvoke/assets/stickers/${filename}`, import.meta.url)));
}

/** Compare independent projects by media bytes, after proving each owns its files. */
export async function ownedStructuralSignature(request: APIRequestContext, project: Project): Promise<string> {
  const response = await request.get(`/api/v1/projects/${encodeURIComponent(project.projectId)}/directory`);
  expect(response.ok(), await response.text()).toBeTruthy();
  const directory = await response.json();
  expect(directory.storageKind).toBe('directory');
  const root = realpathSync(directory.mediaDirectory);
  const normalized = structuredClone(project);
  for (const track of normalized.sequence.tracks) for (const clip of track.clips) {
    const source = clip.assetRef.sourcePath;
    if (!source) continue;
    const owned = relative(root, realpathSync(source));
    expect(owned && !owned.startsWith('..') && !isAbsolute(owned), source).toBeTruthy();
    clip.assetRef.sourcePath = `sha256:${createHash('sha256').update(readFileSync(source)).digest('hex')}`;
  }
  return structuralSignature(normalized);
}
