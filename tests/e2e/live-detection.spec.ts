import { test, expect, Page } from '@playwright/test';
import { resolve } from 'node:path';
import AxeBuilder from '@axe-core/playwright';

async function openLiveDetection(page: Page) {
  await page.goto('/');
  await page.getByLabel(/^Email/).fill('manager@harbour.demo');
  await page.getByLabel('Password', { exact: true }).fill('AisleDemo!2026');
  await page.getByRole('button', { name: 'Open demo workspace' }).click();
  if ((page.viewportSize()?.width ?? 1280) < 800) {
    await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
  }
  await page.getByRole('button', { name: 'LIVE DETECTION', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Live Detection', level: 1, exact: true })).toBeVisible();
}

test('dedicated tab has explicit inputs, no automatic camera access, and bounded recording validation', async ({ page }) => {
  let cameraCalls = 0;
  await page.exposeFunction('recordUnexpectedCameraAccess', () => cameraCalls++);
  await page.addInitScript(() => {
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = (...args) => {
      (window as any).recordUnexpectedCameraAccess();
      return original(...args);
    };
  });
  await openLiveDetection(page);
  await expect(page.getByRole('button', { name: 'Share CCTV screen', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Connect camera', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Start detection', exact: true })).toBeDisabled();
  await expect(page.getByLabel('Alert on restricted-zone entry')).not.toBeChecked();
  await page.getByLabel('Choose CCTV recording').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('This is not CCTV.') });
  await expect(page.getByRole('alert')).toContainText('Choose an MP4 or WebM video');
  await page.getByLabel('Choose CCTV recording').setInputFiles({ name: 'empty.mp4', mimeType: 'video/mp4', buffer: Buffer.from('') });
  await expect(page.getByRole('alert')).toContainText('not empty');
  await page.getByLabel('Choose CCTV recording').setInputFiles(resolve('tests/fixtures/synthetic-video.webm'));
  await expect(page.getByText(/^Recorded CCTV ready\./)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Start detection', exact: true })).toBeEnabled();
  await expect(page.getByText('DETECTION STOPPED', { exact: true })).toBeVisible();
  expect(cameraCalls).toBe(0);
  const accessibility = await new AxeBuilder({ page }).include('.live-detection').analyze();
  expect(accessibility.violations).toEqual([]);
});

test('changing recordings and leaving the tab releases local object URLs without uploading video', async ({ page }) => {
  await page.addInitScript(() => {
    const urls = new Set<string>();
    (window as any).__liveTestBlobs = urls;
    const create = URL.createObjectURL.bind(URL);
    const revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = source => { const url = create(source); urls.add(url); return url; };
    URL.revokeObjectURL = url => { urls.delete(url); revoke(url); };
  });
  await openLiveDetection(page);
  const videoWrites: string[] = [];
  page.on('request', request => {
    if (request.method() !== 'GET' && /multipart|video\//.test(request.headers()['content-type'] ?? '')) videoWrites.push(request.url());
  });
  const fixture = resolve('tests/fixtures/synthetic-video.webm');
  await page.getByLabel('Choose CCTV recording').setInputFiles(fixture);
  await expect(page.getByText(/^Recorded CCTV ready\./)).toBeVisible();
  await page.getByLabel('Choose CCTV recording').setInputFiles(fixture);
  await expect(page.getByText(/^Recorded CCTV ready\./)).toBeVisible();
  expect(await page.evaluate(() => (window as any).__liveTestBlobs.size)).toBe(1);
  await page.getByRole('button', { name: 'Stop detection', exact: true }).click();
  await expect(page.getByText('Detection stopped.', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Overview', exact: true }).click();
  expect(await page.evaluate(() => (window as any).__liveTestBlobs.size)).toBe(0);
  expect(videoWrites).toEqual([]);
});

test('camera permission cancellation has an actionable error and leaves detection stopped', async ({ page }) => {
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('Camera permission denied', 'NotAllowedError'));
  });
  await openLiveDetection(page);
  await page.getByRole('button', { name: 'Connect camera', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Access was cancelled or blocked');
  await expect(page.getByRole('button', { name: 'Start detection', exact: true })).toBeDisabled();
  await expect(page.getByText('DETECTION STOPPED', { exact: true })).toBeVisible();
});

test('mobile detection controls and event log fit without horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openLiveDetection(page);
  await expect(page.getByRole('button', { name: 'Load CCTV video', exact: true })).toBeVisible();
  await page.getByLabel('Alert on restricted-zone entry').check();
  await page.getByLabel('Left %', { exact: true }).fill('95');
  await expect(page.getByLabel('Width %', { exact: true })).toHaveValue('5');
  await expect(page.getByRole('heading', { name: 'Detection event log', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  const accessibility = await new AxeBuilder({ page }).include('.live-detection').analyze();
  expect(accessibility.violations).toEqual([]);
});
