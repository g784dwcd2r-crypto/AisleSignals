# Synthetic video fixture

`synthetic-video.webm` is an original, generated test asset: a pale rectangle alternates positions on a plain dark background. It contains no people, pharmacy footage, camera feeds, audio, or third-party images. It does not demonstrate theft detection or establish detection accuracy. No third-party video licence is required for this generated fixture; the repository owner retains the project's distribution decisions.

- Container / codec: WebM / VP8, no audio.
- Resolution: 320 × 180 pixels.
- Duration: 6 seconds, 60 frames at 10 frames per second.
- Size: 7,151 bytes.
- SHA-256: `6ba4cea8d3f71c5fa20b1454b51478dcb41f7f082da70042e5d5224d5bc035d1`.
- Pattern: initially stationary; alternating rectangle positions every half-second during the middle four seconds; stationary at the end. This exercises visible frame changes, quiet intervals, real media decoding and timestamp navigation.

## Regeneration

After `npm ci` and `npx playwright install chromium`, save the script below as `.generate-synthetic-video.cjs` in the repository root. Run `node .generate-synthetic-video.cjs /path/to/ffmpeg`, then remove the script. Use an existing FFmpeg with MJPEG decoding and VP8 encoding; this fixture was generated with Playwright's bundled FFmpeg. The script draws every pixel with a browser canvas and encodes exactly 60 generated frames. No remote media is accessed. Browser/encoder changes may change the bytes; update the checksum and size after intentional regeneration.

```javascript
const fs = require('node:fs');
const { spawnSync } = require('node:child_process');
const { chromium } = require('./node_modules/playwright-core');

(async () => {
  if (!process.argv[2]) throw new Error('Pass the path to an existing FFmpeg executable.');
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const pictures = await page.evaluate(() => [20, 180].map(x => {
    const canvas = document.createElement('canvas');
    canvas.width = 320;
    canvas.height = 180;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#20292b';
    ctx.fillRect(0, 0, 320, 180);
    ctx.fillStyle = '#f3f4e9';
    ctx.fillRect(x, 35, 115, 110);
    return canvas.toDataURL('image/jpeg', 0.95).split(',')[1];
  }));
  await browser.close();
  const frames = Array.from({ length: 60 }, (_, frame) => {
    const position = frame < 10 ? 0 : frame < 50 ? Math.floor((frame - 10) / 5) % 2 : 1;
    return Buffer.from(pictures[position], 'base64');
  });
  const output = 'tests/fixtures/synthetic-video.webm';
  const run = spawnSync(process.argv[2], [
    '-hide_banner', '-loglevel', 'error', '-f', 'image2pipe',
    '-framerate', '10', '-c:v', 'mjpeg', '-i', 'pipe:0',
    '-an', '-c:v', 'libvpx', '-b:v', '100k', '-g', '10',
    '-threads', '1', '-f', 'webm', '-y', output,
  ], { input: Buffer.concat(frames) });
  if (run.status !== 0) throw new Error(run.error?.message ?? run.stderr.toString());
  console.log(JSON.stringify({ bytes: fs.statSync(output).size }));
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});

```

## Case-review recording

`synthetic-case-video.webm` repeats the original six-second generated fixture ten times with continuous media timestamps: 60 seconds, 600 frames, WebM/VP8, 320 × 180, no audio. Size: 67,204 bytes. SHA-256: `b077ba9a9cd88b9c484e772d2409c2490f73fffe940503885e25d8a561b0711c`. It contains the same original rectangle pixels and no customer or third-party footage.

The case-review browser journeys use this longer recording so the source remains active while a real local API job completes and staff inspect the result. The short scanner fixture could naturally end after sampling and stop live result polling, racing the case-review assertion on slower Windows runners. The synthetic case provider also waits two seconds to exercise asynchronous completion beyond that short input window. Source-end cancellation remains enabled and tested separately; this asset does not change application timing or detection guards.

Regenerate from the checked-in original with an existing FFmpeg:

```sh
ffmpeg -hide_banner -loglevel error -stream_loop 9 -i tests/fixtures/synthetic-video.webm -map 0:v:0 -c:v copy -an -y tests/fixtures/synthetic-case-video.webm
```

This remuxes the generated video without fetching remote media. Encoder/container versions may change output bytes; record the new checksum after intentional regeneration.
