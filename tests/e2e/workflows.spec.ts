import { test, expect, Page, type Request as PlaywrightRequest } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import AxeBuilder from '@axe-core/playwright';

async function login(page: Page, email = 'manager@harbour.demo') {
  await page.goto('/');
  await page.getByLabel(/^Email/).fill(email);
  await page.getByLabel('Password', { exact: true }).fill('AisleDemo!2026');
  await page.getByRole('button', { name: 'Open demo workspace' }).click();
  await expect(page.getByRole('main').getByRole('heading', { level: 1 })).toBeVisible();
  await expect(page.getByRole('main')).toBeVisible();
}

async function createManual(page: Page, title: string) {
  await page.getByRole('button', { name: 'New manual case', exact: true }).first().click();
  const modal = page.getByRole('dialog');
  await modal.getByLabel('Case title').fill(title);
  await modal.getByLabel('Initial notes').fill('Synthetic test: review the shelf count. No loss has been established.');
  await modal.getByRole('button', { name: 'Create manual case' }).click();
  await expect(modal).not.toBeVisible();
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
}

test('returned item is dismissed without creating a case', async ({ page }) => {
  await login(page);
  const before = await (await page.request.get('/api/bootstrap')).json();
  await page.getByRole('button', { name: /^Review queue/ }).click();
  await page.getByRole('button').filter({ hasText: 'Item returned to shelf' }).first().click();
  await expect(page.getByRole('img', { name: /Synthetic illustration/ })).toBeVisible();
  await page.getByLabel('Your review reason').fill('Synthetic review confirms the item was returned; no loss established.');
  await page.getByRole('button', { name: 'Dismiss observation', exact: true }).click();
  await expect(page.getByText('A reviewer dismissed this observation.', { exact: false })).toBeVisible();
  const after = await (await page.request.get('/api/bootstrap')).json();
  expect(after.incidents.length).toBe(before.incidents.length);
  expect(after.candidates.find((c: { scenario: string }) => c.scenario === 'RETURNED_ITEM').status).toBe('DISMISSED');
});

test('manual case, task, closure, approved final report and export', async ({ page }) => {
  await login(page);
  const title = 'Synthetic stock review journey';
  await createManual(page, title);
  await page.getByRole('combobox', { name: 'Classification', exact: true }).selectOption('BENIGN');
  await page.getByRole('combobox', { name: 'Outcome', exact: true }).selectOption('NO_LOSS_ESTABLISHED');
  await page.getByRole('button', { name: 'Save case details' }).click();
  await expect(page.getByText('All case details saved.', { exact: true })).toBeVisible();
  await page.getByLabel('Task title', { exact: true }).fill('Confirm the synthetic count');
  await page.getByLabel('Assigned colleague', { exact: true }).fill('Demo Colleague');
  await page.getByLabel('Due date (your device time)', { exact: true }).fill('2026-10-01T12:00');
  await page.getByRole('button', { name: 'Add follow-up task' }).click();
  await expect(page.getByRole('button', { name: 'Close case', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Complete task: Confirm the synthetic count', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Close case', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Close case', exact: true }).click();
  await page.getByRole('dialog').getByLabel('Reason', { exact: true }).fill('Synthetic count checked and all follow-up completed.');
  await page.getByRole('button', { name: 'Confirm close case' }).click();
  await expect(page.getByRole('button', { name: 'Reopen case', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Generate local template', exact: true }).click();
  await page.getByRole('button', { name: 'Approve reviewed draft', exact: true }).click();
  await expect(page.getByText('Reviewed and approved', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Export case JSON', exact: true }).click();
  await page.getByRole('dialog').getByLabel('Export purpose', { exact: true }).fill('Synthetic prototype acceptance review.');
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download case JSON' }).click();
  const download = await downloaded;
  const record = JSON.parse(await readFile((await download.path())!, 'utf8'));
  expect(record.record.incident.title).toBe(title);
  expect(record.record.incident.status).toBe('CLOSED');
  expect(record.record.incident.draft.approved).toBe(true);
  expect(record.record.provenance).toContain('SYNTHETIC');
  expect(record.sha256).toMatch(/^[a-f0-9]{64}$/);
});

test('missing media and historical observations stay explicit', async ({ page }) => {
  await login(page);
  await page.getByRole('button', { name: /^Review queue/ }).click();
  await page.getByRole('button').filter({ hasText: 'Observation with missing media' }).first().click();
  await expect(page.getByText('Evidence unavailable', { exact: true })).toBeVisible();
  await expect(page.getByRole('img', { name: /Synthetic illustration/ })).toHaveCount(0);
  await page.getByRole('button').filter({ hasText: 'Historical observation' }).first().click();
  await expect(page.getByText('Historical · no live alert', { exact: true })).toBeVisible();
});

test('outage preserves unsaved case input and disables writes', async ({ page }) => {
  await login(page);
  await createManual(page, 'Synthetic offline recovery');
  const notes = page.getByLabel('Reviewed notes');
  await notes.fill('Unsaved synthetic input must survive an interrupted connection.');
  await page.route('**/api/bootstrap', route => route.abort('connectionrefused'));
  await page.getByRole('button', { name: 'Refresh workspace', exact: true }).click();
  await expect(page.getByText('Connection lost · coverage unknown', { exact: true })).toBeVisible();
  await expect(notes).toHaveValue('Unsaved synthetic input must survive an interrupted connection.');
  await expect(page.getByRole('button', { name: 'Save case details' })).toBeDisabled();
  await page.unroute('**/api/bootstrap');
  await page.getByRole('button', { name: 'Refresh workspace', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Save case details' })).toBeEnabled();
  await expect(notes).toHaveValue('Unsaved synthetic input must survive an interrupted connection.');
  await page.getByRole('button', { name: 'Save case details' }).click();
  await expect(page.getByText('All case details saved.', { exact: true })).toBeVisible();
});

test('background updates preserve edits and require conflict reconciliation', async ({ page }) => {
  await login(page);
  await createManual(page, 'Synthetic conflicting staff edits');
  await page.getByLabel('Reviewed notes').fill('My unsaved local interpretation remains visible.');
  const session = await (await page.request.get('/api/session')).json();
  const data = await (await page.request.get('/api/bootstrap')).json();
  const incident = data.incidents.find((i: { title: string }) => i.title === 'Synthetic conflicting staff edits');
  const write = await page.request.patch(`/api/incidents/${incident.id}`, {
    headers: { Origin: new URL(page.url()).origin, 'X-CSRF-Token': session.csrf_token },
    data: { expected_version: incident.version, notes: 'Another staff reviewer recorded new synthetic context.' },
  });
  expect(write.status()).toBe(200);
  await page.getByRole('button', { name: 'Refresh workspace', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Load latest record', exact: true })).toBeVisible();
  await expect(page.getByLabel('Reviewed notes')).toHaveValue('My unsaved local interpretation remains visible.');
  await expect(page.getByRole('button', { name: 'Save case details' })).toBeDisabled();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'Load latest record', exact: true }).click();
  await expect(page.getByLabel('Reviewed notes')).toHaveValue('Another staff reviewer recorded new synthetic context.');
});

test('reviewer restrictions and organisation switch are visible and enforced', async ({ page }) => {
  await login(page, 'reviewer@harbour.demo');
  await createManual(page, 'Synthetic reviewer owned case');
  await expect(page.getByLabel('Recorded loss (€)', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Export case JSON', exact: true })).toHaveCount(0);
  const own = await (await page.request.get('/api/bootstrap')).json();
  const evidence = own.candidates.find((c: { evidence_url: string | null }) => c.evidence_url)?.evidence_url;
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await login(page, 'manager@liffey.demo');
  const other = await (await page.request.get('/api/bootstrap')).json();
  expect(other.site.name).toBe('Liffey Pharmacy');
  expect(other.incidents.some((i: { title: string }) => i.title === 'Synthetic reviewer owned case')).toBe(false);
  expect((await page.request.get(evidence)).status()).toBe(404);
});

test('narrow display keeps the sign-in form and workspace usable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  const widths = await page.evaluate(() => ({ full: document.documentElement.scrollWidth, view: innerWidth }));
  expect(widths.full).toBeLessThanOrEqual(widths.view + 1);
  await expect(page.getByRole('button', { name: 'New manual case', exact: true }).first()).toBeVisible();
});

test('reviewer can add notes to a manager-confirmed case without changing its values', async ({ page }) => {
  await login(page);
  const title = 'Synthetic confirmed loss with reviewer follow-up';
  await createManual(page, title);
  await page.getByRole('combobox', { name: 'Classification', exact: true }).selectOption('STORE_CONFIRMED_LOSS');
  await page.getByRole('combobox', { name: 'Outcome', exact: true }).selectOption('LOSS_RECORDED');
  await page.getByLabel('Recorded loss (€)', { exact: true }).fill('12.00');
  await page.getByRole('button', { name: 'Save case details' }).click();
  await expect(page.getByText('All case details saved.', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await login(page, 'reviewer@harbour.demo');
  await page.getByRole('button', { name: 'Casebook', exact: true }).click();
  await page.getByRole('button').filter({ hasText: title }).first().click();
  await page.getByLabel('Reviewed notes').fill('Additional synthetic review note; manager financial values remain unchanged.');
  await page.getByRole('button', { name: 'Save case details' }).click();
  await expect(page.getByText('All case details saved.', { exact: true })).toBeVisible();
  const data = await (await page.request.get('/api/bootstrap')).json();
  const incident = data.incidents.find((i: { title: string }) => i.title === title);
  expect(incident.classification).toBe('STORE_CONFIRMED_LOSS');
  expect(incident.loss_cents).toBe(1200);
  expect(incident.notes).toContain('Additional synthetic review note');
});

async function revokeAndOpenOtherAccount(page: Page) {
  const session = await (await page.request.get('/api/session')).json();
  const revoked = await page.request.post('/api/logout', {
    headers: { Origin: new URL(page.url()).origin, 'X-CSRF-Token': session.csrf_token }, data: {},
  });
  expect(revoked.status()).toBe(200);
  // Run the existing authenticated refresh behind the busy export dialog. No
  // navigation: the document and outstanding export continuation stay alive.
  await page.clock.fastForward('00:11');
  await expect(page.getByRole('button', { name: 'Open demo workspace' })).toBeVisible();
  await page.getByLabel(/^Email/).fill('manager@liffey.demo');
  await page.getByLabel('Password', { exact: true }).fill('AisleDemo!2026');
  await page.getByRole('button', { name: 'Open demo workspace' }).click();
  await expect(page.getByRole('main').getByRole('heading', { level: 1 })).toBeVisible();
  const current = await (await page.request.get('/api/bootstrap')).json();
  expect(current.site.name).toBe('Liffey Pharmacy');
}

async function expectNoPreviousExport(page: Page, downloads: () => number, title: string) {
  await page.waitForEvent('download', { timeout: 750 }).then(() => { throw new Error('Prior-session download escaped its session'); }, () => {});
  expect(downloads()).toBe(0);
  const current = await (await page.request.get('/api/bootstrap')).json();
  expect(current.site.name).toBe('Liffey Pharmacy');
  expect(current.incidents.some((incident: { title: string }) => incident.title === title)).toBe(false);
  await expect(page.getByText('Synthetic case JSON downloaded.', { exact: false })).toHaveCount(0);
}

// FR-002 / FR-032: prove a delivered old body is rejected independently of the
// network timeout. The real API, request signal and export bytes are retained.
test('late export from an expired session cannot download into another account', async ({ page }) => {
  await page.clock.install();
  await page.addInitScript(() => {
    const nativeFetch = window.fetch.bind(window);
    window.fetch = async (...args: Parameters<typeof fetch>) => {
      const response = await nativeFetch(...args);
      const input = args[0];
      const url = new URL(typeof input === 'string' ? input : input instanceof Request ? input.url : input.href, location.href);
      if (args[1]?.method !== 'POST' || !/^\/api\/incidents\/[^/]+\/export$/.test(url.pathname)) return response;
      // Drain the native response before gating application consumption. This
      // keeps AbortSignal.timeout intact and makes delivery deterministic even
      // if a slow account switch takes longer than the transport deadline.
      const blob = await response.blob();
      const record = JSON.parse(await blob.text());
      let release!: () => void;
      const gate = new Promise<void>(resolve => { release = resolve; });
      const held = { captured: false, delivered: false, status: response.status, title: record.record.incident.title, release };
      (window as any).__heldCaseExport = held;
      Object.defineProperty(response, 'blob', { value: async () => {
        held.captured = true;
        await gate;
        held.delivered = true;
        return blob;
      } });
      return response;
    };
  });
  await login(page);
  const title = 'Synthetic delayed export session boundary';
  await createManual(page, title);
  let downloads = 0;
  page.on('download', () => downloads++);
  await page.getByRole('button', { name: 'Export case JSON', exact: true }).click();
  await page.getByRole('dialog').getByLabel('Export purpose', { exact: true }).fill('Synthetic delayed response regression.');
  await page.getByRole('button', { name: 'Download case JSON' }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__heldCaseExport?.captured)).toBe(true);
  expect(await page.evaluate(() => ({ status: (window as any).__heldCaseExport.status, title: (window as any).__heldCaseExport.title }))).toEqual({ status: 200, title });
  await revokeAndOpenOtherAccount(page);
  await page.evaluate(() => (window as any).__heldCaseExport.release());
  await expect.poll(() => page.evaluate(() => (window as any).__heldCaseExport.delivered)).toBe(true);
  await expectNoPreviousExport(page, () => downloads, title);
});

// FR-002 / FR-032: separately require the real 12-second signal to abort the
// captured request. An aborted request has no response event to wait for.
test('timed-out export from an expired session cannot affect another account', async ({ page }) => {
  await page.clock.install();
  await page.addInitScript(() => {
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (...args: Parameters<typeof fetch>) => {
      const input = args[0];
      const url = new URL(typeof input === 'string' ? input : input instanceof Request ? input.url : input.href, location.href);
      if (args[1]?.method === 'POST' && /^\/api\/incidents\/[^/]+\/export$/.test(url.pathname)) {
        const signal = args[1]?.signal;
        (window as any).__exportAbortReason = null;
        signal?.addEventListener('abort', () => { (window as any).__exportAbortReason = signal.reason?.name; }, { once: true });
      }
      return nativeFetch(...args);
    };
  });
  await login(page);
  const title = 'Synthetic timed-out export session boundary';
  await createManual(page, title);
  let releaseExport!: () => void;
  let exportCaptured!: () => void;
  const release = new Promise<void>(resolve => { releaseExport = resolve; });
  const captured = new Promise<void>(resolve => { exportCaptured = resolve; });
  let downloads = 0, routeReleased = false;
  let exportRequest: PlaywrightRequest | undefined;
  let exportFailure: string | null = null;
  page.on('download', () => downloads++);
  page.on('requestfailed', request => {
    if (request === exportRequest) exportFailure = request.failure()?.errorText ?? null;
  });
  await page.route('**/api/incidents/*/export', async route => {
    exportRequest = route.request();
    const response = await route.fetch();
    expect(response.status()).toBe(200);
    exportCaptured();
    await release;
    await route.fulfill({ response });
    routeReleased = true;
  });
  await page.getByRole('button', { name: 'Export case JSON', exact: true }).click();
  await page.getByRole('dialog').getByLabel('Export purpose', { exact: true }).fill('Synthetic native timeout regression.');
  await page.getByRole('button', { name: 'Download case JSON' }).click();
  await captured;
  await revokeAndOpenOtherAccount(page);
  await page.clock.fastForward('00:13');
  await expect.poll(() => page.evaluate(() => (window as any).__exportAbortReason), { timeout: 13000 }).toBe('TimeoutError');
  await expect.poll(() => exportFailure).toBe('net::ERR_ABORTED');
  releaseExport();
  await expect.poll(() => routeReleased).toBe(true);
  await expectNoPreviousExport(page, () => downloads, title);
});

test('login, overview and review view pass automated accessibility checks', async ({ page }) => {
  await page.goto('/');
  const audit = () => new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect((await audit()).violations).toEqual([]);
  await login(page);
  expect((await audit()).violations).toEqual([]);
  await page.getByRole('button', { name: /^Review queue/ }).click();
  await page.getByRole('button').filter({ hasText: 'Shelf interaction for review' }).first().click();
  expect((await audit()).violations).toEqual([]);
});
