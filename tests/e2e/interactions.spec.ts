import { test, expect, type Page } from '@playwright/test';
import { resolve } from 'node:path';

const jobId = 'dddddddd-1111-4111-8111-111111111111';
const resultId = 'eeeeeeee-1111-4111-8111-111111111111';

async function open(page: Page) {
  await page.goto('/');
  await page.getByLabel(/^Email/).fill('manager@harbour.demo');
  await page.getByLabel('Password', { exact: true }).fill('AisleDemo!2026');
  await page.getByRole('button', { name: 'Open demo workspace' }).click();
  await page.getByRole('button', { name: 'LIVE DETECTION', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Understand the sampled interaction' })).toBeVisible();
}

// Provider responses below are explicit fixtures for workflow tests. They do not
// demonstrate model accuracy. Actual pretrained inference is tested separately.
async function modelWorkflow(page: Page, { concealment = false, delayed = false } = {}) {
  let submitted: any = null;
  let item: any = null;
  let cancelled = 0;
  let deleted = false;
  await page.addInitScript(() => {
    (window as any).__interactionAudioStarts = 0;
    const start = OscillatorNode.prototype.start;
    OscillatorNode.prototype.start = function (...args) {
      (window as any).__interactionAudioStarts++;
      return start.apply(this, args);
    };
    // Empty poses isolate product sampling from person/pose gates.
    class EmptyPoseWorker {
      onmessage: ((event: any) => void) | null = null;
      onerror = null;
      postMessage(request: any) {
        request.bitmap?.close();
        queueMicrotask(() => this.onmessage?.({ data: request.type === 'init' ? { type: 'ready' } : { type: 'result', id: request.id, poses: [] } }));
      }
      terminate() {}
    }
    (window as any).Worker = EmptyPoseWorker;
  });
  await page.route('**/api/interactions/status', route => route.fulfill({ json: { ready: true, model: 'qwen3-vl:4b', mode: 'experimental', message: 'Controlled test provider; no accuracy claim.' } }));
  await page.route('**/api/interactions', route => route.fulfill({ json: { items: item && !deleted ? [item] : [] } }));
  await page.route('**/api/interactions/jobs', async route => {
    submitted = route.request().postDataJSON();
    expect(route.request().headers()['x-csrf-token']).toBeTruthy();
    expect(route.request().headers()['idempotency-key']).toMatch(/^[a-f0-9-]{36}$/i);
    const action = concealment ? 'POSSIBLE_CONCEALMENT' : 'NORMAL_SHOPPING';
    item = {
      id: resultId, run_id: submitted.run_id, source_kind: submitted.source_kind,
      source_label: submitted.source_label, created_at: new Date().toISOString(),
      expires_at: new Date(Date.now() + 86400000).toISOString(), model: 'qwen3-vl:4b',
      action, visibility: 'clear', person_visible: true, product_visible: concealment, sequence_observed: concealment,
      reason: 'Controlled model-boundary fixture. Review the sampled frames.',
      evidence_frame_indices: concealment ? [0, 3] : [], alarm_eligible: concealment,
      inference_ms: 1000, review: null, version: 1, validated: false, provenance: 'experimental_local_vlm',
      frames: submitted.frames.map((frame: any, index: number) => ({ at_seconds: frame.at_seconds, url: `/api/interactions/${resultId}/frames/${index}` })),
    };
    await route.fulfill({ json: { id: jobId, status: 'pending' } });
  });
  await page.route(`**/api/interactions/jobs/${jobId}`, async route => {
    if (delayed) await new Promise(resolve => setTimeout(resolve, 1800));
    await route.fulfill({ json: { id: jobId, status: 'completed', result: item } }).catch(() => {});
  });
  await page.route(`**/api/interactions/jobs/${jobId}/cancel`, async route => {
    cancelled++;
    await route.fulfill({ json: { id: jobId, status: 'cancelled' } });
  });
  await page.route(`**/api/interactions/${resultId}/frames/*`, async route => {
    const index = Number(route.request().url().split('/').at(-1));
    await route.fulfill({ contentType: 'image/jpeg', body: Buffer.from(submitted.frames[index].jpeg_base64, 'base64') });
  });
  await page.route(`**/api/interactions/${resultId}/review`, async route => {
    item.review = route.request().postDataJSON();
    await route.fulfill({ json: item });
  });
  await page.route(`**/api/interactions/${resultId}`, async route => {
    expect(route.request().method()).toBe('DELETE');
    deleted = true;
    await route.fulfill({ json: { deleted: true } });
  });
  return { submitted: () => submitted, cancelled: () => cancelled };
}

async function collect(page: Page) {
  await page.getByLabel('Choose CCTV recording').setInputFiles(resolve('tests/fixtures/synthetic-video.webm'));
  await page.getByLabel('Enable product interaction analysis', { exact: true }).check();
  await page.getByRole('button', { name: 'Start detection', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Analyse recent sequence', exact: true })).toBeEnabled({ timeout: 12000 });
}

test('product analysis samples actual video without pose gates, displays evidence, accepts review and deletes', async ({ page }) => {
  const probe = await modelWorkflow(page);
  await open(page);
  await expect(page.getByLabel('Enable product interaction analysis', { exact: true })).not.toBeChecked();
  await expect(page.getByLabel('Analyse automatically', { exact: true })).not.toBeChecked();
  await expect(page.getByLabel('Experimental product attention alarm', { exact: true })).not.toBeChecked();
  await collect(page);
  await page.getByRole('button', { name: 'Analyse recent sequence', exact: true }).click();
  await expect(page.locator('.interaction-result')).toHaveCount(1);
  const payload = probe.submitted();
  expect(payload.source_kind).toBe('RECORDED_VIDEO');
  expect(payload.frames).toHaveLength(4);
  for (let i = 0; i < payload.frames.length; i++) {
    expect(Buffer.from(payload.frames[i].jpeg_base64, 'base64').subarray(0, 2).toString('hex')).toBe('ffd8');
    if (i) expect(payload.frames[i].at_seconds).toBeGreaterThan(payload.frames[i - 1].at_seconds);
  }
  await page.locator('.interaction-result summary').click();
  await expect(page.locator('.interaction-result img')).toHaveCount(4);
  await expect.poll(() => page.locator('.interaction-result img').evaluateAll(images => images.every(image => (image as HTMLImageElement).naturalWidth > 0))).toBe(true);
  await page.locator('.interaction-result').getByRole('button', { name: 'Normal shopping', exact: true }).click();
  await expect(page.locator('.interaction-result').getByRole('button', { name: 'Normal shopping', exact: true })).toHaveAttribute('aria-pressed', 'true');
  expect(await page.evaluate(() => (window as any).__interactionAudioStarts)).toBe(0);
  await page.locator('.interaction-result').getByRole('button', { name: 'Delete result & frames', exact: true }).click();
  await expect(page.locator('.interaction-result')).toHaveCount(0);
  await page.getByRole('button', { name: 'Stop detection', exact: true }).click();
});

test('explicit experimental alarm requests audio only for a fresh eligible result', async ({ page }) => {
  await modelWorkflow(page, { concealment: true });
  await open(page);
  await collect(page);
  await page.getByLabel('Experimental product attention alarm', { exact: true }).check();
  await page.getByRole('button', { name: 'Analyse recent sequence', exact: true }).click();
  await expect(page.locator('.interaction-alert')).toBeVisible();
  await expect.poll(() => page.evaluate(() => (window as any).__interactionAudioStarts)).toBe(1);
  await page.getByRole('button', { name: 'Silence product alarm', exact: true }).click();
  await page.getByRole('button', { name: 'Stop detection', exact: true }).click();
  await expect(page.getByLabel('Experimental product attention alarm', { exact: true })).not.toBeChecked();
});

test('stopping cancels the pending job and a late concealment response cannot sound', async ({ page }) => {
  const probe = await modelWorkflow(page, { concealment: true, delayed: true });
  await open(page);
  await collect(page);
  await page.getByLabel('Experimental product attention alarm', { exact: true }).check();
  await page.getByRole('button', { name: 'Analyse recent sequence', exact: true }).click();
  await expect.poll(() => probe.submitted()).not.toBeNull();
  await page.getByRole('button', { name: 'Stop detection', exact: true }).click();
  await expect.poll(() => probe.cancelled()).toBeGreaterThan(0);
  await expect(page.getByRole('button', { name: 'Analysing…', exact: true })).toHaveCount(0);
  await expect(page.locator('.interaction-alert')).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__interactionAudioStarts)).toBe(0);
});

test('camera-area selection crops sampled source pixels and changing the area clears pending context', async ({ page }) => {
  const probe = await modelWorkflow(page);
  await open(page);
  await page.getByLabel('Choose CCTV recording').setInputFiles(resolve('tests/fixtures/synthetic-video.webm'));
  await page.getByLabel('Enable product interaction analysis', { exact: true }).check();
  await page.getByLabel('Analyse this camera/aisle area', { exact: true }).check();
  await page.getByLabel('Analysis width %', { exact: true }).fill('50');
  await page.getByLabel('Analysis height %', { exact: true }).fill('50');
  await page.getByRole('button', { name: 'Start detection', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Analyse recent sequence', exact: true })).toBeEnabled({ timeout: 12000 });
  const source = await page.locator('video').first().evaluate(video => ({ width: video.videoWidth, height: video.videoHeight }));
  await page.getByRole('button', { name: 'Analyse recent sequence', exact: true }).click();
  await expect(page.locator('.interaction-result')).toHaveCount(1);
  expect(probe.submitted().source_label).toContain('selected area');
  await page.locator('.interaction-result summary').click();
  const image = page.locator('.interaction-result img').first();
  await expect.poll(() => image.evaluate(element => (element as HTMLImageElement).naturalWidth)).toBe(Math.round(source.width / 2));
  await expect.poll(() => image.evaluate(element => (element as HTMLImageElement).naturalHeight)).toBe(Math.round(source.height / 2));
  await page.getByLabel('Experimental product attention alarm', { exact: true }).check();
  await page.getByLabel('Analysis left %', { exact: true }).fill('10');
  await expect(page.getByLabel('Experimental product attention alarm', { exact: true })).not.toBeChecked();
  await page.getByRole('button', { name: 'Stop detection', exact: true }).click();
});
