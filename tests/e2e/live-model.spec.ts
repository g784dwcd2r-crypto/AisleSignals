import { test, expect, type Page } from '@playwright/test';
import { resolve } from 'node:path';

const fixture = resolve('tests/fixtures/synthetic-video.webm');

async function open(page: Page) {
  await page.goto('/');
  await page.getByLabel(/^Email/).fill('manager@harbour.demo');
  await page.getByLabel('Password', { exact: true }).fill('AisleDemo!2026');
  await page.getByRole('button', { name: 'Open demo workspace' }).click();
  await page.getByRole('button', { name: 'LIVE DETECTION', exact: true }).click();
  await page.getByLabel('Choose CCTV recording').setInputFiles(fixture);
  await expect(page.getByRole('button', { name: 'Start detection', exact: true })).toBeEnabled();
}

// Isolate the model boundary for deterministic event/audio/lifecycle tests.
// These fixtures are synthetic body coordinates; production has no test hook.
async function syntheticPoseWorker(page: Page, delayInit = false) {
  await page.addInitScript(({ delayInit }) => {
    const state = { workers: 0, soundStarts: 0, frames: 0 };
    (window as any).__liveProbe = state;
    const actualStart = OscillatorNode.prototype.start;
    OscillatorNode.prototype.start = function (...args) {
      state.soundStarts++;
      return actualStart.apply(this, args);
    };
    class TestWorker {
      onmessage: ((event: any) => void) | null = null;
      onerror = null;
      closed = false;
      constructor() { state.workers++; }
      postMessage(request: any) {
        if (request.type === 'init') {
          if (!delayInit) queueMicrotask(() => this.onmessage?.({ data: { type: 'ready' } }));
          return;
        }
        if (this.closed) { request.bitmap?.close(); return; }
        request.bitmap?.close(); state.frames++;
        const landmarks = Array.from({ length: 33 }, () => ({ x: .5, y: .2, visibility: .99 }));
        for (const [i,x,y] of [[11,.42,.32],[12,.58,.32],[13,.39,.48],[14,.61,.48],[15,.38,.82],[16,.62,.82],[23,.44,.6],[24,.56,.6],[25,.43,.77],[26,.57,.77],[27,.42,.95],[28,.58,.95]])
          landmarks[i] = {x,y,visibility:.99};
        queueMicrotask(() => this.onmessage?.({ data: { type: 'result', id: request.id, persons: [{ box: { x: .2, y: .1, width: .6, height: .88 }, detectorScore: .95, subjectPixels: 32000, visibilityState: 'sufficient', landmarks }] } }));
      }
      terminate() { if (!this.closed) { state.workers--; this.closed=true; } }
    }
    (window as any).Worker = TestWorker;
  }, { delayInit });
}

test('real local model processes decoded video with no people or fabricated alarms', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await open(page);
  const urls: string[] = [];
  const posts: string[] = [];
  page.on('request', request => {
    urls.push(request.url());
    if (request.method() === 'POST' && request.url().endsWith('/live-events')) posts.push(request.postData() ?? '');
  });
  await page.getByRole('button', { name: 'Start detection', exact: true }).click();
  await expect(page.getByText('POSE TRACKING RUNNING', { exact: true })).toBeVisible();
  await expect(page.getByText(/^No person detected/)).toBeVisible();
  await expect.poll(() => page.locator('.ld-metrics b').nth(2).textContent()).not.toBe('0');
  await page.getByRole('button', { name: 'Stop detection', exact: true }).click();
  await expect(page.getByText('DETECTION STOPPED', { exact: true })).toBeVisible();
  expect(posts).toEqual([]);
  expect(errors).toEqual([]);
  expect(urls.some(url => url.includes('pose_landmarker_lite.task'))).toBe(true);
  expect(urls.some(url => url.includes('efficientdet_lite0_uint8.tflite'))).toBe(true);
  expect(urls.some(url => url.endsWith('.wasm'))).toBe(true);
  expect(urls.filter(url => /^https?:/.test(url)).every(url => new URL(url).origin === new URL(page.url()).origin)).toBe(true);
  await expect(page.locator('.ld-track')).toHaveCount(0);
});

test('temporal zone event creates an automatic alarm, scoped log and acknowledgement', async ({ page }) => {
  await syntheticPoseWorker(page);
  await open(page);
  await page.getByLabel('Sound on movement-rule alerts', { exact: true }).check();
  await page.getByLabel('Alert on restricted-zone entry').check();
  await page.getByLabel('Left %', { exact: true }).fill('0');
  await page.getByLabel('Top %', { exact: true }).fill('0');
  await page.getByLabel('Width %', { exact: true }).fill('100');
  await page.getByLabel('Height %', { exact: true }).fill('100');
  const before = await (await page.request.get('/api/live-events')).json();
  const response = page.waitForResponse(r => r.url().endsWith('/api/live-events') && r.request().method() === 'POST');
  await page.getByRole('button', { name: 'Start detection', exact: true }).click();
  await expect(page.locator('.ld-track')).toHaveCount(1);
  const saved = await (await response).json();
  expect(saved.event_code).toBe('RESTRICTED_ZONE_ENTRY');
  expect(saved.source_kind).toBe('RECORDED_VIDEO');
  expect(saved.sound_requested).toBe(true);
  expect(saved.source_time_seconds).toBeGreaterThanOrEqual(2);
  expect(saved).not.toHaveProperty('media');
  await expect(page.locator('.ld-active-alert')).toBeVisible();
  await expect.poll(() => page.evaluate(() => (window as any).__liveProbe.soundStarts)).toBe(1);
  const row = page.locator('.ld-event').filter({ hasText: saved.source_label }).filter({ has: page.getByRole('button', { name: 'Acknowledge', exact: true }) }).first();
  await row.getByRole('button', { name: 'Acknowledge', exact: true }).click();
  await expect(page.locator('.ld-active-alert')).toHaveCount(0);
  await page.getByRole('button', { name: 'Stop detection', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__liveProbe.workers)).toBe(0);
  const after = await (await page.request.get('/api/live-events')).json();
  expect(after.length).toBe(before.length + 1);
  expect(after.find((r: any) => r.id === saved.id).acknowledged_at).not.toBeNull();
  await page.waitForTimeout(600);
  expect(await page.evaluate(() => (window as any).__liveProbe.soundStarts)).toBe(1);
});

test('stopping during model startup immediately terminates the worker and permits retry', async ({ page }) => {
  await syntheticPoseWorker(page, true);
  await open(page);
  await page.getByRole('button', { name: 'Start detection', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__liveProbe.workers)).toBe(1);
  await page.getByRole('button', { name: 'Stop detection', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__liveProbe.workers)).toBe(0);
  await expect(page.getByRole('button', { name: 'Start detection', exact: true })).toBeEnabled();
  await expect(page.getByText('DETECTION STOPPED', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Overview', exact: true }).click();
  expect(await page.evaluate(() => (window as any).__liveProbe.soundStarts)).toBe(0);
});

test('shared live input logs its source correctly and disconnect stops inference and sound', async ({ page }) => {
  await syntheticPoseWorker(page);
  await page.addInitScript(() => {
    navigator.mediaDevices.getDisplayMedia = async () => {
      const canvas = document.createElement('canvas'); canvas.width=640; canvas.height=360;
      const context = canvas.getContext('2d')!;
      let frame=0;
      const timer=setInterval(()=>{context.fillStyle=frame++%2?'#142822':'#183028';context.fillRect(0,0,640,360);},100);
      const stream=canvas.captureStream(10);
      (window as any).__disconnectCCTV=()=>{clearInterval(timer);for(const track of stream.getTracks()){track.stop();track.dispatchEvent(new Event('ended'));}};
      return stream;
    };
  });
  await open(page);
  await page.getByRole('button', { name: 'Share CCTV screen', exact: true }).click();
  await expect(page.getByText(/^Live CCTV screen ready\./)).toBeVisible();
  await page.getByRole('button', { name: 'Mute', exact: true }).click();
  await page.getByLabel('Alert on restricted-zone entry').check();
  for(const label of ['Left %','Top %']) await page.getByLabel(label,{exact:true}).fill('0');
  for(const label of ['Width %','Height %']) await page.getByLabel(label,{exact:true}).fill('100');
  const response=page.waitForResponse(r=>r.url().endsWith('/api/live-events')&&r.request().method()==='POST');
  await page.getByRole('button',{name:'Start detection',exact:true}).click();
  const saved=await(await response).json();
  expect(saved.source_kind).toBe('SCREEN_CAPTURE');
  expect(saved.sound_requested).toBe(false);
  await page.evaluate(()=>(window as any).__disconnectCCTV());
  await expect(page.getByText('DETECTION STOPPED',{exact:true})).toBeVisible();
  await expect(page.getByText('No live frames are being analysed.',{exact:true})).toBeVisible();
  await expect.poll(()=>page.evaluate(()=>(window as any).__liveProbe.workers)).toBe(0);
  expect(await page.evaluate(()=>(window as any).__liveProbe.soundStarts)).toBe(0);
});
