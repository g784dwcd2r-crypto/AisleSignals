// Build-time download only. Runtime inference never fetches external resources.
import { createHash } from "node:crypto";
import { cp, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const target = join(root, "apps/web/public/vision");
const source = join(root, "apps/web/node_modules/@mediapipe/tasks-vision");
const version = JSON.parse(
  await readFile(join(source, "package.json"), "utf8"),
).version;
if (version !== "0.10.35")
  throw new Error("Expected pinned MediaPipe runtime 0.10.35.");
await mkdir(target, { recursive: true });
await cp(join(source, "wasm"), join(target, "wasm"), { recursive: true });
const checksum = (bytes) => createHash("sha256").update(bytes).digest("hex");
async function verifiedAsset({
  file,
  url,
  expectedBytes,
  expectedSha256,
  maximumBytes,
}) {
  const modelPath = join(target, file);
  let existing;
  try {
    existing = await readFile(modelPath);
  } catch {
    /* First build. */
  }
  if (
    existing &&
    existing.length === expectedBytes &&
    checksum(existing) === expectedSha256
  )
    return;
  const response = await fetch(url, { signal: AbortSignal.timeout(60000) });
  if (!response.ok)
    throw new Error(`${file} download failed (${response.status}).`);
  const chunks = [];
  let length = 0;
  for await (const chunk of response.body) {
    length += chunk.length;
    if (length > maximumBytes)
      throw new Error(`${file} exceeds its pinned size limit.`);
    chunks.push(chunk);
  }
  const bytes = Buffer.concat(chunks);
  if (bytes.length !== expectedBytes || checksum(bytes) !== expectedSha256)
    throw new Error(
      `${file} checksum mismatch; refusing to build with a different model.`,
    );
  const temporary = `${modelPath}.${process.pid}.tmp`;
  try {
    await writeFile(temporary, bytes);
    await rename(temporary, modelPath);
  } finally {
    await rm(temporary, { force: true });
  }
}
await verifiedAsset({
  file: "pose_landmarker_lite.task",
  url: "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
  expectedBytes: 5777746,
  expectedSha256:
    "59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a",
  maximumBytes: 6_000_000,
});
await verifiedAsset({
  file: "efficientdet_lite0_uint8.tflite",
  url: "https://storage.googleapis.com/mediapipe-tasks/object_detector/efficientdet_lite0_uint8.tflite",
  expectedBytes: 4563519,
  expectedSha256:
    "2e04c53bfeac0ac2a30c057c7e2a777594ce39baaac35a92f74fb1e8c4fc4e0b",
  maximumBytes: 5_000_000,
});
console.log(
  "Local vision assets ready: MediaPipe 0.10.35, verified EfficientDet-Lite0 and pose-lite models.",
);
