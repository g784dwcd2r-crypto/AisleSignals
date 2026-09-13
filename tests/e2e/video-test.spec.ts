import { test, expect, Page } from '@playwright/test';
import { resolve } from 'node:path';
import { readFile } from 'node:fs/promises';
import AxeBuilder from '@axe-core/playwright';

const fixture = resolve('tests/fixtures/synthetic-video.webm');

async function openVideoTest(page: Page) {
  await page.goto('/');
  await page.getByLabel(/^Email/).fill('manager@harbour.demo');
  await page.getByLabel('Password', { exact: true }).fill('AisleDemo!2026');
  await page.getByRole('button', { name: 'Open demo workspace' }).click();
  if ((page.viewportSize()?.width ?? 1280) < 800) {
    await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
  }
  await page.getByRole('button', { name: 'Video test', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Video test', exact: true, level: 1 })).toBeVisible();
}

async function chooseFixture(page: Page) {
  await page.getByLabel('Choose a video', { exact: true }).setInputFiles(fixture);
  await expect(page.getByText('Video ready', { exact: true })).toBeVisible();
}

// Record resource lifetimes without replacing the browser's real blob or media implementation.
async function observeBlobURLs(page: Page) {
  await page.addInitScript(() => {
    const active = new Set<string>();
    (window as any).__videoTestBlobs = active;
    const create = URL.createObjectURL.bind(URL);
    const revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = (source) => {
      const url = create(source);
      active.add(url);
      return url;
    };
    URL.revokeObjectURL = (url) => {
      active.delete(url);
      revoke(url);
    };
  });
}

// Deliberately hold analyser seeks so cancellation is tested at an actual async boundary.
// Preview decoding remains real. This instrumentation is only in the browser test context.
async function holdAnalysisSeeks(page: Page) {
  await page.evaluate(() => {
    const descriptor = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime')!;
    (window as any).__videoTestHeldSeeks = [];
    (window as any).__videoTestHoldSeeks = true;
    Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', {
      ...descriptor,
      set(value: number) {
        if ((window as any).__videoTestHoldSeeks && this.getAttribute('aria-label') !== 'Selected test video' && value > 0) {
          (window as any).__videoTestHeldSeeks.push(() => descriptor.set!.call(this, value));
          return;
        }
        descriptor.set!.call(this, value);
      },
    });
  });
}

async function releaseAnalysisSeeks(page: Page) {
  await page.evaluate(() => {
    (window as any).__videoTestHoldSeeks = false;
    for (const resume of (window as any).__videoTestHeldSeeks.splice(0)) resume();
  });
}

test('local video analysis finds timestamped activity without uploads or live records', async ({ page }) => {
  await observeBlobURLs(page);
  await openVideoTest(page);
  const before = await (await page.request.get('/api/bootstrap')).json();
  const requests: { url: string; method: string; body: string | null }[] = [];
  page.on('request', request => {
    if (/^https?:/.test(request.url())) requests.push({ url: request.url(), method: request.method(), body: request.postData() });
  });
  await chooseFixture(page);
  const video = page.getByLabel('Selected test video', { exact: true });
  await expect(video).toBeVisible();
  expect(await video.evaluate((element: HTMLVideoElement) => element.duration)).toBeGreaterThan(5);
  await page.getByRole('button', { name: 'Analyse video', exact: true }).click();
  await expect(page.getByText('Analysis complete', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Activity timeline', exact: true })).toBeVisible();
  const jump = page.getByRole('button', { name: /^Jump to \d{1,2}:\d{2}$/ }).first();
  await expect(jump).toBeVisible();
  const timestamp = (await jump.getAttribute('aria-label')) ?? (await jump.innerText());
  const time = timestamp.match(/(\d{1,2}):(\d{2})/)!;
  await jump.click();
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => Math.floor(element.currentTime))).toBe(Number(time[1]) * 60 + Number(time[2]));
  const after = await (await page.request.get('/api/bootstrap')).json();
  expect(after.candidates.length).toBe(before.candidates.length);
  expect(after.incidents.length).toBe(before.incidents.length);
  expect(after.assistance.length).toBe(before.assistance.length);
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download test summary', exact: true }).click();
  const download = await downloaded;
  const summary = JSON.parse(await readFile((await download.path())!, 'utf8'));
  expect(summary.mode).toBe('RECORDED_PLAYBACK_TEST');
  expect(summary.analysis).toContain('no AI or theft classification');
  expect(summary.file_name).toBe('synthetic-video.webm');
  expect(summary.activity_segments.length).toBeGreaterThan(0);
  expect(summary).not.toHaveProperty('video_bytes');
  expect(requests.filter(request => !['GET', 'HEAD'].includes(request.method))).toEqual([]);
  expect(requests.every(request => !`${request.url}${request.body ?? ''}`.includes('synthetic-video'))).toBe(true);
  expect(requests.every(request => new URL(request.url).origin === 'http://127.0.0.1:8799')).toBe(true);
  await page.getByRole('button', { name: 'Remove video', exact: true }).click();
  await expect(video).toHaveCount(0);
  await expect(page.getByText('Analysis complete', { exact: true })).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => (window as any).__videoTestBlobs.size)).toBe(0);
});

test('undecodable video gives a recoverable error and can be replaced with a valid file', async ({ page }) => {
  await observeBlobURLs(page);
  await openVideoTest(page);
  await page.getByLabel('Choose a video', { exact: true }).setInputFiles({
    name: 'synthetic-corrupt.webm', mimeType: 'video/webm', buffer: Buffer.from('This is synthetic invalid video data.'),
  });
  await expect(page.getByText('This video could not be decoded. Try an MP4 (H.264) or WebM file supported by this browser.', { exact: true })).toBeVisible();
  await chooseFixture(page);
  await page.getByRole('button', { name: 'Analyse video', exact: true }).click();
  await expect(page.getByText('Analysis complete', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Remove video', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__videoTestBlobs.size)).toBe(0);
});

test('cancelled analysis cannot publish late results and can be restarted', async ({ page }) => {
  await openVideoTest(page);
  await chooseFixture(page);
  await holdAnalysisSeeks(page);
  await page.getByRole('button', { name: 'Analyse video', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__videoTestHeldSeeks.length)).toBeGreaterThan(0);
  await page.getByRole('button', { name: 'Cancel analysis', exact: true }).click();
  await expect(page.getByText('Analysis cancelled', { exact: true })).toBeVisible();
  await releaseAnalysisSeeks(page);
  await expect(page.getByText('Analysis complete', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Analyse video', exact: true }).click();
  await expect(page.getByText('Analysis complete', { exact: true })).toBeVisible();
});

test('leaving video test cancels work and sign-out releases selected footage', async ({ page }) => {
  await observeBlobURLs(page);
  await openVideoTest(page);
  await chooseFixture(page);
  await holdAnalysisSeeks(page);
  await page.getByRole('button', { name: 'Analyse video', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__videoTestHeldSeeks.length)).toBeGreaterThan(0);
  await page.getByRole('button', { name: 'Overview', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__videoTestBlobs.size)).toBe(0);
  await releaseAnalysisSeeks(page);
  await page.getByRole('button', { name: 'Video test', exact: true }).click();
  await expect(page.getByLabel('Selected test video', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Analysis complete', { exact: true })).toHaveCount(0);
  await chooseFixture(page);
  await page.getByLabel('Selected test video', { exact: true }).evaluate(async (element: HTMLVideoElement) => {
    (window as any).__videoTestDetachedPreview = element;
    await element.play();
  });
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Open demo workspace', exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => (window as any).__videoTestBlobs.size)).toBe(0);
  expect(await page.evaluate(() => ({
    paused: (window as any).__videoTestDetachedPreview.paused,
    src: (window as any).__videoTestDetachedPreview.getAttribute('src'),
  }))).toEqual({ paused: true, src: null });
});

test('video test remains accessible and fits a narrow display with results', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openVideoTest(page);
  await chooseFixture(page);
  await page.getByRole('button', { name: 'Analyse video', exact: true }).click();
  await expect(page.getByText('Analysis complete', { exact: true })).toBeVisible();
  expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations).toEqual([]);
  const widths = await page.evaluate(() => ({ full: document.documentElement.scrollWidth, view: innerWidth }));
  expect(widths.full).toBeLessThanOrEqual(widths.view + 1);
});
