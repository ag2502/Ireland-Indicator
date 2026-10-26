// Redraws every app icon and splash screen from the sign-plate mark in web/app/icon.tsx.
// It overwrites the PNGs `cap add` created, at their existing paths and sizes, so the
// native projects need no other changes. Run from mobile/: `npm run icons`.
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import sharp from "sharp";

const SIGN = "#00553f";

// The mark on a 32-unit grid, as in icon.tsx: white keyline plate, yellow range, black
// likely dot, white official-time notch.
const plate = `
  <rect x="4.3" y="4.3" width="23.4" height="23.4" rx="2.2" fill="none" stroke="#fff" stroke-width="1.6"/>
  <rect x="7.5" y="13" width="17" height="6" rx="1" fill="#ffd400"/>
  <circle cx="15.4" cy="16" r="2.4" fill="#000"/>
  <rect x="20.5" y="11" width="1.2" height="10" fill="#fff"/>`;

// `scale` is the plate's share of the canvas; `bg` is "square", "circle" or "none".
function svg(w, h, { scale, bg }) {
  const s = (Math.min(w, h) * scale) / 32;
  const backdrop =
    bg === "square" ? `<rect width="${w}" height="${h}" fill="${SIGN}"/>`
    : bg === "circle" ? `<circle cx="${w / 2}" cy="${h / 2}" r="${Math.min(w, h) / 2}" fill="${SIGN}"/>`
    : "";
  return Buffer.from(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">${backdrop}` +
      `<g transform="translate(${w / 2 - 16 * s} ${h / 2 - 16 * s}) scale(${s})">${plate}</g></svg>`,
  );
}

// Android's adaptive icon shows only the middle 72 of 108 dp, masked to any shape, so its
// plate must fit inside a 72 dp circle; 0.52 keeps the keyline clear of its edge.
const styles = {
  "ic_launcher.png": { scale: 1, bg: "square" },
  "ic_launcher_round.png": { scale: 0.85, bg: "circle" },
  "ic_launcher_foreground.png": { scale: 0.52, bg: "none" },
  "splash.png": { scale: 0.3, bg: "square" },
  "AppIcon-512@2x.png": { scale: 1, bg: "square" }, // iOS rounds the corners itself
};

function* pngs(dir) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) yield* pngs(p);
    else if (e.name.endsWith(".png")) yield p;
  }
}

const style = (name) => styles[name] ?? (name.startsWith("splash-") ? styles["splash.png"] : undefined);

let n = 0;
for (const dir of ["android/app/src/main/res", "ios/App/App/Assets.xcassets"]) {
  for (const p of pngs(dir)) {
    const st = style(p.split("/").pop());
    if (!st) continue;
    const { width, height } = await sharp(p).metadata();
    let img = sharp(svg(width, height, st));
    if (p.endsWith("AppIcon-512@2x.png")) img = img.flatten({ background: SIGN }); // App Store rejects alpha
    writeFileSync(p, await img.png().toBuffer());
    n++;
  }
}

const bg = "android/app/src/main/res/values/ic_launcher_background.xml";
writeFileSync(bg, readFileSync(bg, "utf8").replace(/#[0-9A-Fa-f]{6}/, SIGN.toUpperCase()));
console.log(`redrew ${n} images`);
