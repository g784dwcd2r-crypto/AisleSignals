import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const dist = join(root, "apps/web/dist");
const expected = new Map([
  [
    "efficientdet_lite0_uint8.tflite",
    "2e04c53bfeac0ac2a30c057c7e2a777594ce39baaac35a92f74fb1e8c4fc4e0b",
  ],
  [
    "pose_landmarker_lite.task",
    "59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a",
  ],
]);
for (const [file, digest] of expected) {
  const bytes = await readFile(join(dist, "vision", file));
  const actual = createHash("sha256").update(bytes).digest("hex");
  if (actual !== digest) throw new Error(`${file} is missing or unverified.`);
}
const assets = await readdir(join(dist, "assets"));
const workers = assets.filter((file) => /^pose\.worker-.*\.js$/.test(file));
if (workers.length !== 1)
  throw new Error("Expected exactly one bundled person/pose worker.");
const worker = await readFile(join(dist, "assets", workers[0]), "utf8");
for (const file of expected.keys())
  if (!worker.includes(file))
    throw new Error(`Bundled worker does not reference ${file}.`);
if (/https?:\/\//.test(worker))
  throw new Error("Bundled inference worker contains an external network URL.");
console.log("Person-first vision bundle is complete, pinned and same-origin.");
