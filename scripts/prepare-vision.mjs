// Build-time download only. Runtime inference never fetches external resources.
import { createHash } from 'node:crypto';
import { cp, mkdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const target = join(root, 'apps/web/public/vision');
const source = join(root, 'apps/web/node_modules/@mediapipe/tasks-vision');
const version = JSON.parse(await readFile(join(source, 'package.json'), 'utf8')).version;
if (version !== '0.10.35') throw new Error('Expected pinned MediaPipe runtime 0.10.35.');
await mkdir(target, { recursive: true });
await cp(join(source, 'wasm'), join(target, 'wasm'), { recursive: true });
const modelPath = join(target, 'pose_landmarker_lite.task');
const expected = '59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a';
const checksum = (bytes) => createHash('sha256').update(bytes).digest('hex');
let existing;
try { existing = await readFile(modelPath); } catch { /* First build. */ }
if (!existing || checksum(existing) !== expected) {
  const url = 'https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task';
  const response = await fetch(url, { signal: AbortSignal.timeout(60000) });
  if (!response.ok) throw new Error(`Pose model download failed (${response.status}).`);
  const chunks = [];
  let length = 0;
  for await (const chunk of response.body) {
    length += chunk.length;
    if (length > 6_000_000) throw new Error('Pose model exceeds its pinned size limit.');
    chunks.push(chunk);
  }
  const bytes = Buffer.concat(chunks);
  if (bytes.length !== 5777746 || checksum(bytes) !== expected)
    throw new Error('Pose model checksum mismatch; refusing to build with a different model.');
  const temporary = `${modelPath}.${process.pid}.tmp`;
  try { await writeFile(temporary, bytes); await rename(temporary, modelPath); }
  finally { await rm(temporary, { force: true }); }
}
console.log('Local vision assets ready: MediaPipe 0.10.35, verified pose-lite model v1.');
