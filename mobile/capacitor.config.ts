import { readFileSync, writeFileSync } from "node:fs";
import type { CapacitorConfig } from "@capacitor/cli";

// The app is a shell around the live site: it loads BUSTRUST_APP_URL, so every web
// deploy reaches the apps without a store release. Only www/ ships inside the app; it
// holds the page shown when the phone is offline at launch.
//
//   BUSTRUST_APP_URL=https://yourdomain npx cap sync           # store builds
//   BUSTRUST_APP_URL=http://10.0.2.2:3000 npx cap sync android  # emulator, local `npm run dev`
const url = process.env.BUSTRUST_APP_URL;
if (!url) {
  throw new Error("Set BUSTRUST_APP_URL to the site the app should load, e.g. https://bustrust.app");
}
// The offline page is served from the app's own origin, so its retry button needs the
// address. It is written into the page itself: Capacitor serves only the error page from
// the bundle and sends any file it references to the network, which is down.
writeFileSync(
  "www/offline.html",
  readFileSync("src/offline.html", "utf8").replace("__BUSTRUST_APP_URL__", encodeURI(url)),
);

const config: CapacitorConfig = {
  appId: "io.github.ag2502.bustrust", // permanent once published to either store
  appName: "BusTrust",
  webDir: "www",
  backgroundColor: "#e7ebe9",
  server: {
    url,
    cleartext: url.startsWith("http://"), // local testing only; stores need https
    errorPath: "offline.html",
  },
  android: {
    allowMixedContent: false,
  },
  ios: {
    contentInset: "never",
  },
};

export default config;
