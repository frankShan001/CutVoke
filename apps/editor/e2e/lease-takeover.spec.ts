import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
const gate = () => { let release!: () => void; const promise = new Promise<void>(r => { release = r; }); return { promise, release }; };
async function seed(request: APIRequestContext, suffix: string) {
    const id = `takeover-${suffix}-${Date.now()}`;
    expect((await request.post('/api/v1/projects', { data: { projectId: id, name: id, width: 320, height: 180, fps: 30 } })).ok()).toBeTruthy();
    expect((await request.post(`/api/v1/projects/${id}/commands`, { data: { type: 'caption.add', payload: { captionId: 'caption', text: 'Agent 原字幕', start: { num: 0, den: 1 }, end: { num: 2, den: 1 } }, commandId: `${id}-caption`, expectedRevision: '0', actor: { kind: 'agent', id: 'caption-agent' } } })).ok()).toBeTruthy();
    return id;
}
async function lock(request: APIRequestContext, id: string, owner = 'caption-agent') {
    const r = await request.post(`/api/v1/projects/${id}/edit-lock`, { data: { owner, ttlSeconds: 300 } });
    expect(r.ok()).toBeTruthy();
    return (await r.json()).lease;
}
async function open(page: Page, id: string) {
    await page.goto('/');
    await page.getByRole('button', { name: `打开工程 ${id}`, exact: true }).click();
    await expect(page.getByRole('heading', { name: '时间线', exact: true })).toBeVisible({ timeout: 45000 });
}
async function confirm(page: Page) { await page.getByRole('button', { name: '接管编辑', exact: true }).click(); await page.getByRole('button', { name: '确认接管', exact: true }).click(); }
test('human takeover cancels safely, submits once, rejects stale writes and ignores old polls', async ({ page, request }, testInfo) => {
    const id = await seed(request, 'handoff'), lease = await lock(request, id);
    await open(page, id);
    const notice = page.locator('.agent-edit-lock');
    await expect(notice).toContainText('caption-agent');
    await expect(notice).toContainText('租约剩余');
    await page.screenshot({ path: testInfo.outputPath('lease-notice.png') });
    let posts = 0;
    const accepted = gate(), ack = gate();
    await page.route(`**/projects/${id}/edit-lock/takeover`, async (route) => { posts++; const response = await route.fetch(); accepted.release(); await ack.promise; await route.fulfill({ response }); });
    await page.getByRole('button', { name: '接管编辑', exact: true }).click();
    await page.getByRole('button', { name: '取消', exact: true }).click();
    expect(posts).toBe(0);
    expect((await (await request.get(`/api/v1/projects/${id}/edit-lock`)).json()).lease.leaseId).toBe(lease.leaseId);
    const slow = gate(), seen = gate(), delivered = gate();
    let hold = true;
    await page.route(`**/projects/${id}/edit-lock`, async (route) => {
        if (!hold)
            return route.continue();
        const response = await route.fetch();
        seen.release();
        await slow.promise;
        await route.fulfill({ response });
        delivered.release();
    });
    await seen.promise;
    await confirm(page);
    await accepted.promise;
    await expect(page.getByRole('button', { name: '正在接管…', exact: true })).toBeDisabled();
    expect(posts).toBe(1);
    ack.release();
    await expect(notice).toHaveCount(0);
    const stale = await request.post(`/api/v1/projects/${id}/commands`, { data: { type: 'track.add', payload: { trackId: 'late', kind: 'video' }, commandId: `${id}-late`, expectedRevision: '1', editLeaseId: lease.leaseId } });
    expect(stale.status()).toBe(409);
    expect((await stale.json()).error.code).toBe('EDIT_LEASE_INVALID');
    hold = false;
    slow.release();
    await delivered.promise;
    await page.waitForResponse(r => r.url().endsWith(`/projects/${id}/edit-lock`) && r.ok());
    await expect(notice).toHaveCount(0);
    await page.getByRole('tablist', { name: '创作域' }).getByRole('tab', { name: '字幕', exact: true }).click();
    await page.getByRole('button', { name: '选择字幕 caption', exact: true }).click();
    await page.getByRole('textbox', { name: '编辑字幕文字' }).fill('人工接管后修改');
    await expect.poll(async () => (await (await request.get(`/api/v1/projects/${id}`)).json()).sequence.captions[0].text).toBe('人工接管后修改');
    await page.getByRole('button', { name: 'Agent活动面板', exact: true }).click();
    const audit = page.locator('.agent-event').filter({ hasText: 'edit.lease.takenOver' });
    await expect(audit).toHaveCount(1);
    await expect(audit).toContainText('web-editor');
    await expect(audit.getByRole('button', { name: /撤销到此/ })).toHaveCount(0);
    const events = (await (await request.get(`/api/v1/projects/${id}/events?since=0`)).json()).events;
    expect(events.filter((e: {
        type: string;
    }) => e.type === 'edit.lease.takenOver')).toHaveLength(1);
    mkdirSync(resolve('../../.artifacts/checks/lease-takeover'), { recursive: true });
    await page.screenshot({ path: resolve('../../.artifacts/checks/lease-takeover/human-takeover.png') });
});
test('takeover failure preserves the lease and supports retry', async ({ page, request }) => {
    const id = await seed(request, 'failure'), lease = await lock(request, id);
    await open(page, id);
    let fail = true;
    await page.route(`**/projects/${id}/edit-lock/takeover`, route => fail ? route.fulfill({ status: 503, json: { error: { code: 'UNAVAILABLE', message: 'temporary outage' } } }) : route.continue());
    await confirm(page);
    const error = page.getByRole('alertdialog', { name: '操作未完成' });
    await expect(error).toBeVisible();
    await error.getByRole('button', { name: '关闭', exact: true }).click();
    await expect(page.locator('.agent-edit-lock')).toContainText('接管未完成');
    expect((await (await request.get(`/api/v1/projects/${id}/edit-lock`)).json()).lease.leaseId).toBe(lease.leaseId);
    fail = false;
    await page.getByRole('button', { name: '确认接管', exact: true }).click();
    await expect(page.locator('.agent-edit-lock')).toHaveCount(0);
});
test('a successful revoke stays read-only until the refreshed project arrives', async ({ page, request }) => {
    const id = await seed(request, 'refresh');
    await lock(request, id);
    await open(page, id);
    const refresh = gate(), seen = gate();
    let hold = true;
    await page.route(`**/projects/${id}/sync**`, async (route) => {
        if (route.request().method() !== 'GET' || !hold)
            return route.continue();
        seen.release();
        await refresh.promise;
        return route.continue();
    });
    await confirm(page);
    await seen.promise;
    expect((await (await request.get(`/api/v1/projects/${id}/edit-lock`)).json()).locked).toBe(false);
    await expect(page.locator('.agent-edit-lock')).toBeVisible();
    await expect(page.getByRole('button', { name: '正在接管…', exact: true })).toBeDisabled();
    hold = false;
    refresh.release();
    await expect(page.locator('.agent-edit-lock')).toHaveCount(0);
});
test('a stale confirmation cannot break a replacement lease', async ({ page, request }) => {
    const id = await seed(request, 'replacement'), old = await lock(request, id, 'old-agent');
    await open(page, id);
    await page.getByRole('button', { name: '接管编辑', exact: true }).click();
    const seen = gate(), slow = gate();
    let hold = true;
    await page.route(`**/projects/${id}/edit-lock`, async (route) => {
        if (!hold)
            return route.continue();
        const response = await route.fetch();
        seen.release();
        await slow.promise;
        await route.fulfill({ response });
    });
    await seen.promise;
    await request.post(`/api/v1/projects/${id}/edit-lock/release`, { data: { leaseId: old.leaseId } });
    const replacement = await lock(request, id, 'replacement-agent');
    await page.getByRole('button', { name: '确认接管', exact: true }).click();
    await expect(page.locator('.agent-edit-lock')).toContainText('replacement-agent');
    await expect(page.getByRole('alertdialog', { name: '确认接管编辑' })).toHaveCount(0);
    expect((await (await request.get(`/api/v1/projects/${id}/edit-lock`)).json()).lease.leaseId).toBe(replacement.leaseId);
    hold = false;
    slow.release();
    await page.waitForResponse(r => r.url().endsWith(`/projects/${id}/edit-lock`) && r.ok());
    await expect(page.locator('.agent-edit-lock')).toContainText('replacement-agent');
});
for (const reopen of [false, true])
    test(`an old-project poll cannot affect ${reopen ? 'a reopened session' : 'a different project'}`, async ({ page, request }) => {
        const a = await seed(request, 'switch-a'), b = await seed(request, 'switch-b');
        await open(page, a);
        await expect(page.locator('.agent-edit-lock')).toHaveCount(0);
        const seen = gate(), slow = gate(), delivered = gate();
        let hold = true;
        const obsolete = { projectId: a, leaseId: 'obsolete-ui-token', owner: 'obsolete-agent', acquiredAt: Date.now() / 1000, expiresAt: Date.now() / 1000 + 300 };
        await page.route(`**/projects/${a}/edit-lock`, async (route) => {
            if (!hold)
                return route.continue();
            seen.release();
            await slow.promise;
            await route.fulfill({ json: { locked: true, lease: obsolete } });
            delivered.release();
        });
        await seen.promise;
        await page.getByRole('button', { name: '返回工程列表', exact: true }).click();
        await page.getByRole('button', { name: `打开工程 ${reopen ? a : b}`, exact: true }).click();
        await expect(page.getByRole('heading', { name: '时间线', exact: true })).toBeVisible();
        hold = false;
        slow.release();
        await delivered.promise;
        await page.waitForResponse(r => r.url().endsWith(`/projects/${reopen ? a : b}/edit-lock`) && r.ok());
        await expect(page.locator('.agent-edit-lock')).toHaveCount(0);
    });
test('a refresh failure after revoke keeps human controls locked until recovery', async ({ page, request }) => {
    const id = await seed(request, 'refresh-failure');
    await lock(request, id);
    await page.clock.install();
    await open(page, id);
    let fail = true;
    let takeovers = 0;
    // Keep *all* background refreshes failing until the manual retry is sent.
    // Otherwise a successful poll can legitimately remove the retry button.
    await page.route(`**/projects/${id}/edit-lock/takeover`, route => {
        if (++takeovers === 2) fail = false;
        return route.continue();
    });
    await page.route(`**/projects/${id}/sync**`, route => fail ? route.fulfill({ status: 503, json: { error: { code: 'UNAVAILABLE', message: 'refresh unavailable' } } }) : route.continue());
    await confirm(page);
    const error = page.getByRole('alertdialog', { name: '操作未完成' });
    await expect(error).toBeVisible();
    await error.getByRole('button', { name: '关闭', exact: true }).click();
    expect((await (await request.get(`/api/v1/projects/${id}/edit-lock`)).json()).locked).toBe(false);
    await expect(page.locator('.agent-edit-lock')).toContainText('接管未完成');
    const backgroundFailure = page.waitForResponse(response => response.url().includes(`/projects/${id}/sync`) && response.status() === 503);
    await page.clock.runFor(2000);
    await backgroundFailure;
    await expect(error).toHaveCount(0);
    await page.getByRole('button', { name: '确认接管', exact: true }).click();
    expect(takeovers).toBe(2);
    await expect(page.locator('.agent-edit-lock')).toHaveCount(0);
    const events = (await (await request.get(`/api/v1/projects/${id}/events?since=0`)).json()).events;
    expect(events.filter((e: {
        type: string;
    }) => e.type === 'edit.lease.takenOver')).toHaveLength(1);
});
test('lease renewal updates the displayed deadline without changing its identity', async ({ page, request }) => {
    const id = await seed(request, 'renewal');
    const r = await request.post(`/api/v1/projects/${id}/edit-lock`, { data: { owner: 'renewing-agent', ttlSeconds: 20 } });
    expect(r.ok()).toBeTruthy();
    const lease = (await r.json()).lease;
    await open(page, id);
    const remaining = page.locator('.agent-edit-lock small');
    await expect(remaining).toBeVisible();
    const seconds = () => remaining.innerText().then(text => Number(text.match(/\d+/)?.[0]));
    await expect.poll(seconds).toBeLessThanOrEqual(20);
    expect((await request.post(`/api/v1/projects/${id}/edit-lock/renew`, { data: { leaseId: lease.leaseId, ttlSeconds: 300 } })).ok()).toBeTruthy();
    await expect.poll(seconds).toBeGreaterThan(250);
    await expect(page.locator('.agent-edit-lock')).toContainText('renewing-agent');
});
test('a slow lease poll is coalesced instead of being starved by newer requests', async ({ page, request }) => {
    const id = await seed(request, 'slow-poll');
    await lock(request, id, 'slow-agent');
    await page.clock.install();
    const seen = gate(), slow = gate(), delivered = gate();
    let requests = 0;
    await page.route(`**/projects/${id}/edit-lock`, async (route) => { requests++; const response = await route.fetch(); seen.release(); await slow.promise; await route.fulfill({ response }); delivered.release(); });
    await open(page, id);
    await seen.promise;
    await page.clock.runFor(3000);
    expect(requests).toBe(1);
    slow.release();
    await delivered.promise;
    await expect(page.locator('.agent-edit-lock')).toContainText('slow-agent');
});
test('an unresponsive lease poll times out conservatively and learns the replacement', async ({ page, request }) => {
    const id = await seed(request, 'poll-timeout'), old = await lock(request, id, 'original-agent');
    await page.clock.install();
    await open(page, id);
    await expect(page.locator('.agent-edit-lock')).toContainText('original-agent');
    const seen = gate(), release = gate();
    let calls = 0;
    await page.route(`**/projects/${id}/edit-lock`, async (route) => {
        if (++calls > 1)
            return route.continue();
        seen.release();
        await release.promise;
        await route.abort().catch(() => { });
    });
    await page.clock.runFor(1000);
    await seen.promise;
    await request.post(`/api/v1/projects/${id}/edit-lock/release`, { data: { leaseId: old.leaseId } });
    await lock(request, id, 'replacement-agent');
    await expect(page.locator('.agent-edit-lock')).toContainText('original-agent');
    await page.clock.runFor(11000);
    await expect(page.locator('.agent-edit-lock')).toContainText('replacement-agent');
    expect(calls).toBeGreaterThan(1);
    release.release();
});

test('lease review shows only current committed summaries and ignores replaced-lease replies', async ({ page, request }) => {
    const id = await seed(request, 'review'), lease = await lock(request, id, 'review-agent');
    expect((await request.post(`/api/v1/projects/${id}/commands`, { data: {
        type: 'track.add', payload: { trackId: 'lease-track', kind: 'audio' }, commandId: 'review-track',
        expectedRevision: '1', editLeaseId: lease.leaseId, actor: { kind: 'agent', id: 'review-agent' }
    } })).ok()).toBeTruthy();
    await open(page, id);
    const notice = page.locator('.agent-edit-lock');
    await notice.getByRole('button', { name: '查看本次租约的修改', exact: true }).click();
    const panel = notice.getByRole('region', { name: '查看本次租约的修改' });
    await expect(panel).toContainText('租约版本 1 → 2');
    await expect(panel).toContainText('track: created ×1');
    await expect(panel).not.toContainText(`${id}-caption`);
    await expect(panel).not.toContainText('Agent 原字幕');
    mkdirSync(resolve('../../.artifacts/checks/lease-review'), { recursive: true });
    await page.screenshot({ path: resolve('../../.artifacts/checks/lease-review/revisions.png') });

    const held = gate(), arrived = gate(), delivered = gate();
    await page.route(`**/projects/${id}/edit-lock/review`, async route => {
        const response = await route.fetch(); arrived.release(); await held.promise; await route.fulfill({ response }); delivered.release();
    });
    await notice.getByRole('button', { name: '查看本次租约的修改', exact: true }).click();
    await arrived.promise;
    expect((await request.post(`/api/v1/projects/${id}/edit-lock/release`, { data: { leaseId: lease.leaseId } })).ok()).toBeTruthy();
    await lock(request, id, 'replacement-agent');
    await expect(notice).toContainText('replacement-agent', { timeout: 15000 });
    held.release();
    await delivered.promise;
    await expect(panel).toHaveCount(0);
    await page.unroute(`**/projects/${id}/edit-lock/review`);
    await notice.getByRole('button', { name: '查看本次租约的修改', exact: true }).click();
    await expect(panel).toContainText('租约版本 2 → 2');
    await expect(panel).toContainText('没有已提交的修改。');
    await expect(panel).not.toContainText('review-track');
});
