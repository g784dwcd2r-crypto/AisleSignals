import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
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
async function modelWorkflow(page: Page, { concealment = false, delayed = false, delayMs = 1800, reviewConflict = false, unavailable = false } = {}) {
  let submitted: any = null;
  const submissions: { payload: any; receivedAt: number }[] = [];
  let item: any = null;
  let cancelled = 0;
  let deleted = false;
  let reviews = 0;
  let ready = !unavailable;
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
  await page.route('**/api/interactions/status', route => route.fulfill({ json: { ready, model: 'qwen3-vl:4b', mode: ready ? 'experimental' : 'disabled', message: ready ? 'Controlled test provider; no accuracy claim.' : 'Interaction analysis disabled in this controlled test session.', evidence_policy: { retention_seconds: 86400, site_limit: 100, installation_limit: 600, encryption: 'AES-256-GCM', rolling_cleanup: 'Oldest unreviewed ordinary results roll off at capacity. Reviewed samples and possible concealment expire after 24 hours or explicit deletion.' } } }));
  await page.route('**/api/interactions', route => route.fulfill({ json: { items: item && !deleted ? [item] : [] } }));
  await page.route('**/api/interactions/jobs', async route => {
    submitted = route.request().postDataJSON();
    submissions.push({ payload: submitted, receivedAt: Date.now() });
    expect(route.request().headers()['x-csrf-token']).toBeTruthy();
    expect(route.request().headers()['idempotency-key']).toMatch(/^[a-f0-9-]{36}$/i);
    const action = concealment ? 'POSSIBLE_CONCEALMENT' : 'NORMAL_SHOPPING';
    item = {
      id: resultId, run_id: submitted.run_id, source_kind: submitted.source_kind,
      source_label: submitted.source_label, created_at: new Date().toISOString(),
      ...(submitted.camera_calibration ? { camera_calibration: submitted.camera_calibration, camera_calibration_status: 'READY' } : { camera_calibration_status: 'MISSING' }),
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
    if (delayed) await new Promise(resolve => setTimeout(resolve, delayMs));
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
    const request = route.request().postDataJSON();
    expect(request.expected_version).toBe(item.version);
    reviews++;
    if (reviewConflict && reviews === 1) {
      item = { ...item, version: 2, review: { outcome: 'USEFUL', note: 'Another reviewer' } };
      await route.fulfill({ status: 409, json: { error: { code: 'VERSION_CONFLICT', message: 'Review was changed', current_version: 2 } } });
      return;
    }
    item = { ...item, version: item.version + 1, review: { outcome: request.outcome, note: request.note } };
    await route.fulfill({ json: item });
  });
  await page.route(`**/api/interactions/${resultId}`, async route => {
    expect(route.request().method()).toBe('DELETE');
    deleted = true;
    await route.fulfill({ json: { deleted: true } });
  });
  return { submitted: () => submitted, submissions, cancelled: () => cancelled, setReady: (next: boolean) => { ready = next; } };
}

async function commission(page: Page) {
  for (const label of ['Entrance zone is visible', 'Exit zone is visible', 'Cashier zone is visible', 'Relevant shelf zones are visible'])
    await page.getByLabel(label, { exact: true }).check();
  const alarm = page.getByLabel('Experimental product attention alarm', { exact: true });
  await expect(alarm).toBeDisabled();
  await expect(page.getByRole('button', { name: 'I heard the test tone', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Test product alarm sound', exact: true }).click();
  await expect(alarm).toBeDisabled();
  await page.getByRole('button', { name: 'I heard the test tone', exact: true }).click();
  await expect(alarm).toBeDisabled();
  await page.getByLabel('Allow alarm during this recorded-video test', { exact: true }).check();
  await alarm.check();
}

async function collect(page: Page, arm = false) {
  await page.getByLabel('Choose CCTV recording').setInputFiles(resolve('tests/fixtures/synthetic-video.webm'));
  await page.getByRole('combobox', { name: 'Camera layout', exact: true }).selectOption('single');
  await page.getByLabel('Enable product interaction analysis', { exact: true }).check();
  await page.getByRole('button', { name: 'Start detection', exact: true }).click();
  if (arm) await commission(page);
  await expect(page.getByRole('button', { name: 'Analyse recent sequence', exact: true })).toBeEnabled({ timeout: 12000 });
}

// An original, continuously changing canvas source keeps these sampling tests
// independent of the six-second recorded fixture and any physical camera.
async function syntheticCamera(page: Page) {
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = async () => {
      const canvas = document.createElement('canvas');
      canvas.width = 480;
      canvas.height = 270;
      const context = canvas.getContext('2d')!;
      let frame = 0;
      const draw = () => {
        context.fillStyle = `rgb(${30 + (frame * 11) % 170}, 70, 110)`;
        context.fillRect(0, 0, 480, 270);
        context.fillStyle = '#fff';
        context.font = '24px sans-serif';
        context.fillText(`SYNTHETIC SAMPLE ${frame++}`, 24, 120);
      };
      draw();
      const stream = canvas.captureStream(10);
      const timer = setInterval(() => {
        if (stream.getVideoTracks()[0].readyState === 'ended') clearInterval(timer);
        else draw();
      }, 100);
      return stream;
    };
  });
}

async function startSyntheticCamera(page: Page, automatic = false) {
  await open(page);
  await page.getByRole('button', { name: 'Connect camera', exact: true }).click();
  await page.getByRole('combobox', { name: 'Camera layout', exact: true }).selectOption('single');
  await page.getByLabel('Enable product interaction analysis', { exact: true }).check();
  if (automatic) await page.getByLabel('Analyse automatically', { exact: true }).check();
  await page.getByRole('button', { name: 'Start detection', exact: true }).click();
}

test('partial progress advances and the submitted JPEG evidence stays fixed while fresh samples change', async ({ page }) => {
  const probe = await modelWorkflow(page, { delayed: true, delayMs: 3000 });
  await syntheticCamera(page);
  await startSyntheticCamera(page);
  const current = page.getByRole('region', { name: 'Current sampled frames', exact: true });
  const analyse = page.getByRole('button', { name: 'Analyse recent sequence', exact: true });
  for (const count of [1, 2, 3]) {
    await expect(page.getByText(`${count}/4 fresh sampled frames`, { exact: true })).toBeVisible();
    await expect(current.getByRole('img')).toHaveCount(count);
    await expect(analyse).toBeDisabled();
  }
  await expect(page.getByText('4/4 fresh sampled frames', { exact: true })).toBeVisible();
  expect(probe.submitted()).toBeNull();
  await analyse.click();
  await expect.poll(() => probe.submitted()).not.toBeNull();
  const payload = probe.submitted();
  const frozen = page.getByRole('region', { name: 'Submitted sequence', exact: true });
  await expect(frozen.getByRole('img')).toHaveCount(4);
  await expect(frozen).toContainText('Analysing these four frames');
  const expectedImages = payload.frames.map((frame: any) => `data:image/jpeg;base64,${frame.jpeg_base64}`);
  const imageSources = () => frozen.getByRole('img').evaluateAll(images => images.map(image => image.getAttribute('src')));
  expect(await imageSources()).toEqual(expectedImages);
  await expect(frozen.getByText('JPEG 480 × 270 px', { exact: true })).toHaveCount(4);
  await expect.poll(() => frozen.getByRole('img').evaluateAll(images => images.every(image => (image as HTMLImageElement).naturalWidth === 480 && (image as HTMLImageElement).naturalHeight === 270))).toBe(true);
  await expect.poll(() => current.getByRole('img').last().getAttribute('src')).not.toBe(expectedImages[3]);
  expect(await imageSources()).toEqual(expectedImages);
  await expect(frozen).toContainText('Analysis completed');
  await expect(current).toContainText(`Last analysed interval: ${payload.frames[0].at_seconds.toFixed(2)}–${payload.frames[3].at_seconds.toFixed(2)}s`);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByRole('button', { name: 'Stop detection', exact: true }).click();
  await expect(current.getByRole('img')).toHaveCount(0);
  await expect(frozen).toHaveCount(0);
  await expect(current).toContainText('No completed analysis in this camera session yet.');
  expect(await page.evaluate(() => (window as any).__interactionAudioStarts)).toBe(0);
});

test('automatic countdown decreases without shortening the ten-second submission cadence', async ({ page }) => {
  const probe = await modelWorkflow(page);
  await syntheticCamera(page);
  await startSyntheticCamera(page, true);
  await expect.poll(() => probe.submissions.length, { timeout: 12000 }).toBe(1);
  const schedule = page.locator('.interaction-schedule');
  await expect(schedule).toContainText('Next automatic submission in');
  const remaining = Number((await schedule.textContent())!.match(/in (\d+)s/)![1]);
  expect(remaining).toBeGreaterThan(0);
  await expect.poll(async () => Number((await schedule.textContent())?.match(/in (\d+)s/)?.[1] ?? remaining)).toBeLessThan(remaining);
  expect(probe.submissions).toHaveLength(1);
  await expect.poll(() => probe.submissions.length, { timeout: 12000 }).toBe(2);
  // Allow at most 100ms for loopback request dispatch variation; the client
  // continues to enforce its unchanged 10,000ms start-to-start submission gate.
  expect(probe.submissions[1].receivedAt - probe.submissions[0].receivedAt).toBeGreaterThanOrEqual(9900);
  await page.getByLabel('Analyse automatically', { exact: true }).uncheck();
  await expect(schedule).toContainText('Manual test: select Analyse recent sequence');
  await page.getByRole('button', { name: 'Stop detection', exact: true }).click();
});

test('unchecking the camera area clears evidence and requires an explicit new selection', async ({ page }) => {
  const probe = await modelWorkflow(page, { delayed: true });
  await syntheticCamera(page);
  await startSyntheticCamera(page);
  const current = page.getByRole('region', { name: 'Current sampled frames', exact: true });
  await expect(page.getByRole('button', { name: 'Analyse recent sequence', exact: true })).toBeEnabled({ timeout: 12000 });
  await page.getByRole('button', { name: 'Analyse recent sequence', exact: true }).click();
  await expect.poll(() => probe.submitted()).not.toBeNull();
  await page.getByLabel('Analyse this camera/aisle area', { exact: true }).uncheck();
  await expect(page.locator('.interaction-status')).toContainText('No camera area selected.');
  await expect(page.locator('.interaction-status')).not.toContainText('Full frame selected');
  await expect(current.getByRole('img')).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'Submitted sequence', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Start detection', exact: true })).toBeEnabled();
  await expect.poll(() => probe.cancelled()).toBeGreaterThan(0);
  await expect(page.getByRole('button', { name: 'Analysing…', exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__interactionAudioStarts)).toBe(0);
});

test('product analysis samples actual video without pose gates, displays evidence, accepts review and deletes', async ({ page }) => {
  const probe = await modelWorkflow(page);
  await open(page);
  await expect(page.getByLabel('Enable product interaction analysis', { exact: true })).not.toBeChecked();
  await expect(page.getByLabel('Analyse automatically', { exact: true })).not.toBeChecked();
  await expect(page.getByLabel('Experimental product attention alarm', { exact: true })).not.toBeChecked();
  await collect(page);
  await expect(page.getByText('Analysis and recorded demonstrations remain available. Automatic attention sound stays blocked until all four zones are confirmed.', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Analyse recent sequence', exact: true }).click();
  await expect(page.locator('.interaction-result')).toHaveCount(1);
  const payload = probe.submitted();
  expect(payload.source_kind).toBe('RECORDED_VIDEO');
  expect(payload.camera_calibration).toBeUndefined();
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
  await expect(page.getByRole('region', { name: 'Submitted sequence', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Stop detection', exact: true }).click();
});

test('explicit experimental alarm requests audio only for a fresh eligible result', async ({ page }) => {
  const probe = await modelWorkflow(page, { concealment: true });
  await open(page);
  await collect(page, true);
  await page.getByRole('button', { name: 'Analyse recent sequence', exact: true }).click();
  await expect(page.locator('.interaction-alert')).toBeVisible();
  await expect.poll(() => page.evaluate(() => (window as any).__interactionAudioStarts)).toBe(2);
  expect(probe.submitted().camera_calibration).toEqual({ schema_version: '1.0', entrance_zone_confirmed: true, exit_zone_confirmed: true, cashier_zone_confirmed: true, shelf_zones_confirmed: true });
  await expect(page.locator('.interaction-result .interaction-facts')).toContainText('Camera calibration: operator-declared complete');
  await page.getByLabel('Entrance zone is visible', { exact: true }).uncheck();
  await expect(page.getByLabel('Experimental product attention alarm', { exact: true })).not.toBeChecked();
  await page.getByRole('button', { name: 'Silence product alarm', exact: true }).click();
  await page.getByRole('button', { name: 'Acknowledge attention', exact: true }).click();
  await expect(page.locator('.interaction-alert')).toHaveCount(0);
  await page.getByRole('button', { name: 'Refresh model & history', exact: true }).click();
  expect(await page.evaluate(() => (window as any).__interactionAudioStarts)).toBe(2);
  await page.getByRole('button', { name: 'Stop detection', exact: true }).click();
  await expect(page.getByLabel('Experimental product attention alarm', { exact: true })).not.toBeChecked();
});

test('stopping cancels the pending job and a late concealment response cannot sound', async ({ page }) => {
  const probe = await modelWorkflow(page, { concealment: true, delayed: true });
  await open(page);
  await collect(page, true);
  await page.getByRole('button', { name: 'Analyse recent sequence', exact: true }).click();
  await expect.poll(() => probe.submitted()).not.toBeNull();
  await page.getByRole('button', { name: 'Stop detection', exact: true }).click();
  await expect.poll(() => probe.cancelled()).toBeGreaterThan(0);
  await expect(page.getByRole('button', { name: 'Analysing…', exact: true })).toHaveCount(0);
  await expect(page.locator('.interaction-alert')).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__interactionAudioStarts)).toBe(1);
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
  await commission(page);
  await expect(page.getByRole('button', { name: 'Analyse recent sequence', exact: true })).toBeEnabled({ timeout: 12000 });
  const source = await page.locator('video').first().evaluate(video => ({ width: video.videoWidth, height: video.videoHeight }));
  await page.getByRole('button', { name: 'Analyse recent sequence', exact: true }).click();
  await expect(page.locator('.interaction-result')).toHaveCount(1);
  expect(probe.submitted().source_label).toContain('selected area');
  await page.locator('.interaction-result summary').click();
  const image = page.locator('.interaction-result img').first();
  await expect.poll(() => image.evaluate(element => (element as HTMLImageElement).naturalWidth)).toBe(Math.round(source.width / 2));
  await expect.poll(() => image.evaluate(element => (element as HTMLImageElement).naturalHeight)).toBe(Math.round(source.height / 2));
  await page.getByLabel('Analysis left %', { exact: true }).fill('10');
  await expect(page.getByLabel('Experimental product attention alarm', { exact: true })).not.toBeChecked();
  await expect(page.getByRole('button', { name: 'I heard the test tone', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Start detection', exact: true })).toBeEnabled();
  await expect(page.getByText('0/4 fresh sampled frames', { exact: true })).toBeVisible();
});

test('fresh concealment shows visual attention while uncommissioned sound remains off', async ({ page }) => {
  await modelWorkflow(page, { concealment: true });
  await open(page);
  await collect(page);
  await page.getByRole('button', { name: 'Analyse recent sequence', exact: true }).click();
  await expect(page.locator('.interaction-alert')).toBeVisible();
  expect(await page.evaluate(() => (window as any).__interactionAudioStarts)).toBe(0);
  await page.getByRole('button', { name: 'Acknowledge attention', exact: true }).click();
  await expect(page.locator('.interaction-alert')).toHaveCount(0);
  await page.getByRole('button', { name: 'Stop detection', exact: true }).click();
});

test('review conflicts reload the latest version before a second staff decision', async ({ page }) => {
  await modelWorkflow(page, { reviewConflict: true });
  await open(page);
  await expect(page.getByText('Frames are encrypted locally with AES-256-GCM.', { exact: false })).toBeVisible();
  await collect(page);
  await page.getByRole('button', { name: 'Analyse recent sequence', exact: true }).click();
  const item = page.locator('.interaction-result');
  await item.getByRole('button', { name: 'Normal shopping', exact: true }).click();
  await expect(page.getByText('Another reviewer changed this result.', { exact: false })).toBeVisible();
  await expect(item.getByRole('button', { name: 'Useful', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await item.getByRole('button', { name: 'Normal shopping', exact: true }).click();
  await expect(item.getByRole('button', { name: 'Normal shopping', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByText('Another reviewer changed this result.', { exact: false })).toHaveCount(0);
  await page.getByRole('button', { name: 'Stop detection', exact: true }).click();
});

test('disarming during pending audio activation prevents a late test tone', async ({ page }) => {
  await modelWorkflow(page);
  await page.addInitScript(() => {
    const resume = AudioContext.prototype.resume;
    AudioContext.prototype.resume = function () {
      return resume.call(this).then(() => new Promise<void>(resolve => setTimeout(() => {
        (window as any).__productAudioResumeDelivered = true;
        resolve();
      }, 500)));
    };
  });
  await open(page);
  await page.getByLabel('Choose CCTV recording').setInputFiles(resolve('tests/fixtures/synthetic-video.webm'));
  await page.getByLabel('Enable product interaction analysis', { exact: true }).check();
  await page.getByRole('combobox', { name: 'Camera layout', exact: true }).selectOption('single');
  await page.getByRole('button', { name: 'Start detection', exact: true }).click();
  await page.getByRole('button', { name: 'Test product alarm sound', exact: true }).click();
  await page.getByRole('button', { name: 'Stop sound & disarm product alarm', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__productAudioResumeDelivered)).toBe(true);
  expect(await page.evaluate(() => (window as any).__interactionAudioStarts)).toBe(0);
  await expect(page.getByRole('button', { name: 'I heard the test tone', exact: true })).toBeDisabled();
  await expect(page.getByLabel('Experimental product attention alarm', { exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Stop detection', exact: true }).click();
});

test('pose tracking distinguishes unavailable product analysis and recovery needs explicit opt-in', async ({ page }) => {
  const probe = await modelWorkflow(page, { unavailable: true, delayed: true });
  await open(page);
  const productStatus = page.locator('.ld-product-status');
  const enable = page.getByLabel('Enable product interaction analysis', { exact: true });
  await expect(productStatus.getByText('PRODUCT ANALYSIS UNAVAILABLE', { exact: true })).toBeVisible();
  await expect(productStatus).toContainText('Restart with the local interaction model');
  await expect(enable).toBeDisabled();
  await expect(enable).not.toBeChecked();
  await page.getByLabel('Choose CCTV recording').setInputFiles(resolve('tests/fixtures/synthetic-video.webm'));
  await page.getByRole('button', { name: 'Start detection', exact: true }).click();
  await expect(page.getByText('POSE TRACKING RUNNING', { exact: true })).toBeVisible();
  await expect.poll(() => page.locator('.ld-metrics b').nth(2).textContent()).not.toBe('0');
  await expect(productStatus.getByText('PRODUCT ANALYSIS UNAVAILABLE', { exact: true })).toBeVisible();
  await expect(page.getByText('0/4 fresh sampled frames', { exact: true })).toBeVisible();
  expect(probe.submitted()).toBeNull();
  await page.getByRole('button', { name: 'Stop detection', exact: true }).click();
  probe.setReady(true);
  await page.getByRole('button', { name: 'Refresh model & history', exact: true }).click();
  await expect(productStatus.getByText('PRODUCT ANALYSIS OFF', { exact: true })).toBeVisible();
  await expect(enable).toBeEnabled();
  await expect(enable).not.toBeChecked();
  await expect(page.getByLabel('Analyse automatically', { exact: true })).not.toBeChecked();
  await collect(page);
  await expect(productStatus.getByText('PRODUCT ANALYSIS MANUAL · READY', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Analyse recent sequence', exact: true }).click();
  await expect(productStatus.getByText('PRODUCT ANALYSIS ANALYSING', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Stop detection', exact: true }).click();
});

test('losing product model readiness clears enabled sampling and automatic mode without restarting them on recovery', async ({ page }) => {
  const probe = await modelWorkflow(page);
  await open(page);
  const enable = page.getByLabel('Enable product interaction analysis', { exact: true });
  const automatic = page.getByLabel('Analyse automatically', { exact: true });
  await page.getByLabel('Choose CCTV recording').setInputFiles(resolve('tests/fixtures/synthetic-video.webm'));
  await page.getByRole('combobox', { name: 'Camera layout', exact: true }).selectOption('single');
  await enable.check();
  await automatic.check();
  await page.getByRole('button', { name: 'Start detection', exact: true }).click();
  await expect(page.getByText('PRODUCT ANALYSIS AUTOMATIC · COLLECTING', { exact: true })).toBeVisible();
  probe.setReady(false);
  await page.getByRole('button', { name: 'Refresh model & history', exact: true }).click();
  await expect(enable).not.toBeChecked();
  await expect(enable).toBeDisabled();
  await expect(automatic).not.toBeChecked();
  await expect(page.getByText('0/4 fresh sampled frames', { exact: true })).toBeVisible();
  await expect(page.getByText('POSE TRACKING RUNNING', { exact: true })).toBeVisible();
  probe.setReady(true);
  await page.getByRole('button', { name: 'Refresh model & history', exact: true }).click();
  await expect(page.getByText('PRODUCT ANALYSIS OFF', { exact: true })).toBeVisible();
  await expect(enable).not.toBeChecked();
  await expect(automatic).not.toBeChecked();
  expect(probe.submitted()).toBeNull();
  await page.getByRole('button', { name: 'Stop detection', exact: true }).click();
});
