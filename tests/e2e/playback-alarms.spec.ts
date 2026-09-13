import { test, expect, Page } from '@playwright/test';
import { resolve } from 'node:path';

const fixture = resolve('tests/fixtures/synthetic-video.webm');
const path = '/api/playback-events';
type AudioMode = 'running' | 'blocked' | 'held';

// Real video decoding, currentTime and requestAnimationFrame remain untouched.
// Only the speaker boundary is fake: tests never make audible sound.
async function observeAudio(page: Page, mode: AudioMode = 'running') {
  await page.addInitScript((selectedMode) => {
    const state = { starts: 0, disconnects: 0, closes: 0, resumes: 0, releases: [] as (() => void)[] };
    (window as any).__attentionAudio = state;
    const parameter = () => ({ setValueAtTime() {}, linearRampToValueAtTime() {}, cancelScheduledValues() {} });
    class QuietAudioContext {
      state = 'suspended';
      currentTime = 0;
      destination = {};
      onstatechange: (() => void) | null = null;
      resume() {
        state.resumes++;
        if (selectedMode === 'blocked') return Promise.reject(new Error('Synthetic browser audio policy rejection'));
        if (selectedMode === 'held') return new Promise<void>((resolve) => {
          state.releases.push(() => { this.state = 'running'; resolve(); });
        });
        this.state = 'running';
        return Promise.resolve();
      }
      close() { this.state = 'closed'; state.closes++; return Promise.resolve(); }
      createGain() { return { gain: parameter(), connect() {}, disconnect() {} }; }
      createOscillator() {
        return { type: 'sine', frequency: parameter(), onended: null,
          connect() {}, start() { state.starts++; }, stop() {}, disconnect() { state.disconnects++; } };
      }
    }
    (window as any).AudioContext = QuietAudioContext;
  }, mode);
}

async function openVideoTest(page: Page) {
  await page.goto('/');
  await page.getByLabel(/^Email/).fill('manager@harbour.demo');
  await page.getByLabel('Password', { exact: true }).fill('AisleDemo!2026');
  await page.getByRole('button', { name: 'Open demo workspace', exact: true }).click();
  await page.getByRole('button', { name: 'Video test', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Video test', exact: true, level: 1 })).toBeVisible();
}

async function analyseFixture(page: Page) {
  await page.getByLabel('Choose a video', { exact: true }).setInputFiles(fixture);
  await expect(page.getByText('Video ready', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Analyse video', exact: true }).click();
  await expect(page.getByText('Analysis complete', { exact: true })).toBeVisible();
}

async function events(page: Page): Promise<any[]> {
  const response = await page.request.get(path);
  expect(response.ok()).toBe(true);
  return response.json();
}

function observePosts(page: Page) {
  const posts: { body: any; key: string | undefined }[] = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname === path && request.method() === 'POST') {
      posts.push({ body: request.postDataJSON(), key: request.headers()['idempotency-key'] });
    }
  });
  return posts;
}

test('completed scan is silent; armed real playback automatically classifies, sounds once and saves without live incidents', async ({ page }) => {
  await observeAudio(page);
  await openVideoTest(page);
  const before = await (await page.request.get('/api/bootstrap')).json();
  const beforeEvents = await events(page);
  const posts = observePosts(page);
  const start = page.getByRole('button', { name: 'Start alarm playback', exact: true });
  await expect(start).toBeDisabled();
  await analyseFixture(page);
  await expect(start).toBeEnabled();
  expect(posts).toEqual([]);
  expect(await page.evaluate(() => (window as any).__attentionAudio.starts)).toBe(0);
  expect(await events(page)).toEqual(beforeEvents);
  await start.click();
  await expect.poll(() => posts.length).toBe(1);
  await expect.poll(async () => (await events(page)).length).toBe(beforeEvents.length + 1);
  expect(await page.evaluate(() => (window as any).__attentionAudio.starts)).toBe(1);
  expect(posts[0].body).toEqual({
    run_id: expect.any(String), event_index: 0, category: 'SUSTAINED_VISUAL_ACTIVITY',
    video_start_seconds: 1, video_end_seconds: 4.5, peak_changed_ratio: expect.any(Number), alarm_status: 'SOUND_REQUESTED',
  });
  expect(JSON.stringify(posts)).not.toContain('synthetic-video');
  expect(posts[0].body).not.toHaveProperty('filename');
  expect(posts[0].body).not.toHaveProperty('media');
  const video = page.getByLabel('Selected test video', { exact: true });
  // Duplicate time callbacks and a real seek back across the segment must not ring twice.
  await video.evaluate((element: HTMLVideoElement) => {
    element.dispatchEvent(new Event('timeupdate'));
    element.dispatchEvent(new Event('timeupdate'));
    element.currentTime = 0;
  });
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.currentTime)).toBeGreaterThan(2);
  expect(posts).toHaveLength(1);
  expect(await page.evaluate(() => (window as any).__attentionAudio.starts)).toBe(1);
  await page.getByRole('button', { name: 'Stop alarm test', exact: true }).click();
  const after = await (await page.request.get('/api/bootstrap')).json();
  for (const kind of ['candidates', 'incidents', 'assistance']) expect(after[kind]).toEqual(before[kind]);
  const logged = (await events(page)).find(item => item.run_id === posts[0].body.run_id);
  expect(logged.source).toBe('RECORDED_PLAYBACK_TEST');
  expect(logged.provenance).toBe('RULE_BASED_VISUAL_CHANGE_V1');
  await page.getByRole('button', { name: 'Overview', exact: true }).click();
  await page.getByRole('button', { name: 'Video test', exact: true }).click();
  await expect(page.getByText('Sound requested; audibility unverified', { exact: false }).first()).toBeVisible();
  expect((await events(page)).find(item => item.id === logged.id)).toEqual(logged);
});

test('muted playback keeps its visual alert and saves MUTED without requesting a tone', async ({ page }) => {
  await observeAudio(page);
  await openVideoTest(page);
  await analyseFixture(page);
  const posts = observePosts(page);
  await page.getByLabel('Mute alarm sound', { exact: true }).check();
  await expect(page.getByRole('button', { name: 'Test speaker · 2 seconds', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Start alarm playback', exact: true }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0].body.alarm_status).toBe('MUTED');
  await expect(page.getByText('Visual alert recorded; sound is muted.', { exact: true })).toBeVisible();
  await expect.poll(async () => (await events(page)).some(item => item.run_id === posts[0].body.run_id && item.alarm_status === 'MUTED')).toBe(true);
  expect(await page.evaluate(() => (window as any).__attentionAudio.starts)).toBe(0);
  await page.getByRole('button', { name: 'Stop alarm test', exact: true }).click();
});

test('browser audio rejection still records BLOCKED and makes no delivery claim', async ({ page }) => {
  await observeAudio(page, 'blocked');
  await openVideoTest(page);
  await analyseFixture(page);
  const posts = observePosts(page);
  await page.getByRole('button', { name: 'Start alarm playback', exact: true }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0].body.alarm_status).toBe('BLOCKED');
  await expect(page.getByText('Sound was blocked or unavailable. The visual alert remains visible.', { exact: true })).toBeVisible();
  await expect.poll(async () => (await events(page)).some(item => item.run_id === posts[0].body.run_id && item.alarm_status === 'BLOCKED')).toBe(true);
  expect(await page.evaluate(() => (window as any).__attentionAudio.starts)).toBe(0);
  await page.getByRole('button', { name: 'Stop alarm test', exact: true }).click();
});

for (const action of ['stop', 'navigate'] as const) {
  test(`${action} while audio activation is pending prevents late arming, sound and log writes`, async ({ page }) => {
    await observeAudio(page, 'held');
    await openVideoTest(page);
    await analyseFixture(page);
    const posts = observePosts(page);
    const video = page.getByLabel('Selected test video', { exact: true });
    await video.evaluate((element: HTMLVideoElement) => { (window as any).__oldAlarmPreview = element; });
    await page.getByRole('button', { name: 'Start alarm playback', exact: true }).click();
    await expect.poll(() => page.evaluate(() => (window as any).__attentionAudio.releases.length)).toBe(1);
    await page.getByRole('button', { name: action === 'stop' ? 'Stop alarm test' : 'Overview', exact: true }).click();
    await page.evaluate(() => {
      for (const resolve of (window as any).__attentionAudio.releases.splice(0)) resolve();
    });
    if (action === 'stop') {
      await expect(page.getByRole('button', { name: 'Start alarm playback', exact: true })).toBeEnabled();
      await expect(page.getByText('Playback test stopped', { exact: true })).toBeVisible();
    } else {
      await page.getByRole('button', { name: 'Video test', exact: true }).click();
      await expect(page.getByRole('button', { name: 'Start alarm playback', exact: true })).toBeDisabled();
      expect(await page.evaluate(() => (window as any).__attentionAudio.closes)).toBeGreaterThan(0);
    }
    expect(await page.evaluate(() => (window as any).__oldAlarmPreview.paused)).toBe(true);
    expect(await page.evaluate(() => (window as any).__attentionAudio.starts)).toBe(0);
    expect(posts).toEqual([]);
  });
}

test('delayed speaker activation holds the real recording at zero then captures its first activity', async ({ page }) => {
  await observeAudio(page, 'held');
  await openVideoTest(page);
  await analyseFixture(page);
  const posts = observePosts(page);
  await page.getByRole('button', { name: 'Start alarm playback', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__attentionAudio.releases.length)).toBe(1);
  // Deliberately impose speaker activation latency: playback must stay still for
  // the whole interval instead of losing the beginning of the recording.
  const held = await page.getByLabel('Selected test video', { exact: true }).evaluate(async (element: HTMLVideoElement) => {
    const began = performance.now();
    await new Promise(resolve => setTimeout(resolve, 700));
    const snapshot = { paused: element.paused, time: element.currentTime, elapsed: performance.now() - began,
      tones: (window as any).__attentionAudio.starts };
    for (const resume of (window as any).__attentionAudio.releases.splice(0)) resume();
    return snapshot;
  });
  expect(held).toEqual({ paused: true, time: 0, elapsed: expect.any(Number), tones: 0 });
  expect(held.elapsed).toBeGreaterThanOrEqual(650);
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0].body.video_start_seconds).toBe(1);
  expect(posts[0].body.alarm_status).toBe('SOUND_REQUESTED');
  await expect.poll(async () => (await events(page)).some(item => item.run_id === posts[0].body.run_id)).toBe(true);
  expect(await page.evaluate(() => (window as any).__attentionAudio.starts)).toBe(1);
  await page.getByRole('button', { name: 'Stop alarm test', exact: true }).click();
});

test('stopping during a real rewind cancels preparation even when its seek completion arrives late', async ({ page }) => {
  await observeAudio(page);
  await openVideoTest(page);
  await analyseFixture(page);
  const posts = observePosts(page);
  const video = page.getByLabel('Selected test video', { exact: true });
  // Move the real decoder away from zero so Start must perform a real rewind.
  await video.evaluate(async (element: HTMLVideoElement) => {
    await new Promise<void>(resolve => {
      element.addEventListener('seeked', () => resolve(), { once: true });
      element.currentTime = 3;
    });
    (window as any).__heldRewindCompletion = [];
    (window as any).__rewindPlayEvents = 0;
    element.addEventListener('play', () => { (window as any).__rewindPlayEvents++; });
    const add = element.addEventListener.bind(element);
    const remove = element.removeEventListener.bind(element);
    const wrappers = new Map<EventListenerOrEventListenerObject, EventListener>();
    // Only delay delivery of the next real native seeked event to the startup
    // waiter. Native currentTime, decoding, seeking and playback remain real.
    element.addEventListener = ((name: string, listener: EventListenerOrEventListenerObject, options?: AddEventListenerOptions | boolean) => {
      if (name !== 'seeked') return add(name, listener, options);
      const wrapper = (event: Event) => {
        (window as any).__heldRewindCompletion.push(() => {
          if (typeof listener === 'function') listener.call(element, event);
          else listener.handleEvent(event);
        });
      };
      wrappers.set(listener, wrapper);
      add(name, wrapper, options);
    }) as typeof element.addEventListener;
    element.removeEventListener = ((name: string, listener: EventListenerOrEventListenerObject, options?: EventListenerOptions | boolean) => {
      remove(name, wrappers.get(listener) ?? listener, options);
    }) as typeof element.removeEventListener;
  });
  await page.getByRole('button', { name: 'Start alarm playback', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__heldRewindCompletion.length)).toBe(1);
  expect(await video.evaluate((element: HTMLVideoElement) => ({ paused: element.paused, time: element.currentTime, seeking: element.seeking })))
    .toEqual({ paused: true, time: 0, seeking: false });
  await page.getByRole('button', { name: 'Stop alarm test', exact: true }).click();
  await page.evaluate(async () => {
    for (const complete of (window as any).__heldRewindCompletion.splice(0)) complete();
    await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  });
  await expect(page.getByRole('button', { name: 'Start alarm playback', exact: true })).toBeEnabled();
  await expect(page.getByText('Playback test stopped', { exact: true })).toBeVisible();
  expect(await video.evaluate((element: HTMLVideoElement) => element.paused)).toBe(true);
  expect(await page.evaluate(() => (window as any).__rewindPlayEvents)).toBe(0);
  expect(await page.evaluate(() => (window as any).__attentionAudio.starts)).toBe(0);
  expect(posts).toEqual([]);
});

test('lost save response stops playback and retries the same event key without duplicate persistence', async ({ page }) => {
  await observeAudio(page);
  await openVideoTest(page);
  await analyseFixture(page);
  const baseline = await events(page);
  const posts = observePosts(page);
  let loseFirstResponse = true;
  await page.route('**/api/playback-events', async route => {
    if (route.request().method() !== 'POST' || !loseFirstResponse) return route.continue();
    loseFirstResponse = false;
    // The actual API commits, then the browser loses the response.
    const committed = await route.fetch();
    expect(committed.ok()).toBe(true);
    await route.abort('failed');
  });
  await page.getByRole('button', { name: 'Start alarm playback', exact: true }).click();
  await expect(page.getByText('1 event(s) not confirmed saved.', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Start alarm playback', exact: true })).toBeDisabled();
  expect(await page.getByLabel('Selected test video', { exact: true }).evaluate((element: HTMLVideoElement) => element.paused)).toBe(true);
  expect(await events(page)).toHaveLength(baseline.length + 1);
  await page.getByRole('button', { name: 'Retry saving events', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Retry saving events', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Start alarm playback', exact: true })).toBeEnabled();
  expect(posts).toHaveLength(2);
  expect(posts[1]).toEqual(posts[0]);
  expect(posts[0].key).toBeTruthy();
  expect(await events(page)).toHaveLength(baseline.length + 1);
  expect(await page.evaluate(() => (window as any).__attentionAudio.starts)).toBe(1);
});

test('YouTube reference is explicitly loaded and never treated as scanner input', async ({ page }) => {
  await observeAudio(page);
  const external: string[] = [];
  page.on('request', request => {
    if (/^https?:/.test(request.url()) && new URL(request.url()).hostname !== '127.0.0.1') external.push(request.url());
  });
  await page.route('https://www.youtube-nocookie.com/**', route => route.fulfill({
    status: 200, contentType: 'text/html', body: '<!doctype html><title>External player test placeholder</title>',
  }));
  await openVideoTest(page);
  expect(external).toEqual([]);
  await expect(page.getByText('Reference playback only.', { exact: true })).toBeVisible();
  await expect(page.getByText('A YouTube player cannot be scanned by the local analyser.', { exact: false })).toBeVisible();
  await expect(page.locator('iframe')).toHaveCount(0);
  const posts = observePosts(page);
  await page.getByRole('button', { name: 'Load YouTube reference', exact: true }).click();
  const iframe = page.getByTitle('FBI pharmacy CCTV reference — no automatic analysis', { exact: true });
  await expect(iframe).toBeVisible();
  await expect(iframe).toHaveAttribute('src', 'https://www.youtube-nocookie.com/embed/TkS5CyFuumI?autoplay=0');
  await expect(iframe).toHaveAttribute('referrerpolicy', 'strict-origin-when-cross-origin');
  await expect.poll(() => external.length).toBe(1);
  await expect(page.getByRole('button', { name: 'Start alarm playback', exact: true })).toBeDisabled();
  expect(posts).toEqual([]);
  expect(await page.evaluate(() => (window as any).__attentionAudio.starts)).toBe(0);
  await page.getByRole('button', { name: 'Close reference player', exact: true }).click();
  await expect(iframe).toHaveCount(0);
});
