import sharp from "sharp";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const scriptsDir = path.dirname(fileURLToPath(import.meta.url));
const publicDir = path.join(scriptsDir, "..", "public");

const icon = readFileSync(path.join(scriptsDir, "icon.svg"));
const iconMaskable = readFileSync(path.join(scriptsDir, "icon-maskable.svg"));

const jobs = [
  { input: icon, out: "pwa-192x192.png", size: 192 },
  { input: icon, out: "pwa-512x512.png", size: 512 },
  { input: iconMaskable, out: "pwa-512x512-maskable.png", size: 512 },
  { input: icon, out: "apple-touch-icon.png", size: 180 },
  { input: icon, out: "favicon-32x32.png", size: 32 },
  { input: icon, out: "favicon-16x16.png", size: 16 },
];

for (const job of jobs) {
  await sharp(job.input)
    .resize(job.size, job.size)
    .png()
    .toFile(path.join(publicDir, job.out));
  console.log(`wrote ${job.out}`);
}
