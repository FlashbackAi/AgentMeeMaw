// Spike/production CLI: props.json -> MP4 + one still per scene.
// Usage: node render.mjs --props p.json --public-dir pub --out-mp4 o.mp4 --stills-dir st
import { parseArgs } from "node:util";
import { readFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import { bundle } from "@remotion/bundler";
import { selectComposition, renderMedia, renderStill } from "@remotion/renderer";

const { values } = parseArgs({
  options: {
    props: { type: "string" },
    "public-dir": { type: "string" },
    "out-mp4": { type: "string" },
    "stills-dir": { type: "string" },
  },
});

const inputProps = JSON.parse(readFileSync(values.props, "utf-8"));
const publicDir = values["public-dir"];
const stillsDir = values["stills-dir"];
mkdirSync(stillsDir, { recursive: true });
mkdirSync(path.dirname(values["out-mp4"]), { recursive: true });

console.log("[render] bundling…");
const serveUrl = await bundle({
  entryPoint: path.join(process.cwd(), "src", "index.ts"),
  publicDir,
});

const composition = await selectComposition({ serveUrl, id: "Flashback", inputProps });
console.log(`[render] video: ${composition.durationInFrames} frames @ ${composition.fps}fps`);

await renderMedia({
  serveUrl,
  composition,
  codec: "h264",
  outputLocation: values["out-mp4"],
  inputProps,
});
console.log(`[render] wrote ${values["out-mp4"]}`);

const n = inputProps.scenes.length;
const total = composition.durationInFrames;
for (let i = 0; i < n; i++) {
  const frame = Math.min(total - 1, Math.floor((total * (i + 0.5)) / n));
  const output = path.join(stillsDir, `scene_${String(i).padStart(3, "0")}.png`);
  await renderStill({ serveUrl, composition, inputProps, frame, output, imageFormat: "png" });
  console.log(`[render] still ${i} @ frame ${frame}`);
}
console.log("[render] done");
