# mobile: contract

The Android and iOS apps. Each is a Capacitor shell around the live web app: it opens
`BUSTRUST_APP_URL` in a full-screen WebView, so the apps show exactly what the site
shows and every web deploy reaches them without a store release.

## Reads

| What | From |
|---|---|
| Every page and `/v1` call | `BUSTRUST_APP_URL`, set when running `npx cap sync`. Store builds use the production `https://` domain. |
| Location | The phone, through the WebView's `navigator.geolocation`. The Nearby page uses it on the phone and never sends it. |

The app never calls the NTA or the API directly; it only loads the site.

## Ships inside the app

| File | Purpose |
|---|---|
| `www/index.html` | Never shown. Capacitor requires `webDir` to have one. |
| `www/offline.html` | Shown when the site can't load at launch and nothing is cached yet. It is generated from `src/offline.html` with the address filled in, because Capacitor serves only this one page from the bundle. |

Once the site has loaded once, its service worker and `bt:cache:*` keep it working
offline exactly as in a browser.

## Permissions

- Android: `INTERNET`, `ACCESS_COARSE_LOCATION`, `ACCESS_FINE_LOCATION`.
- iOS: `NSLocationWhenInUseUsageDescription`.

Add a permission here when the web app starts using a new device feature.

## Rules for changes

- `appId` (`io.github.ag2502.bustrust`) is permanent once either store has a release.
- Raise `versionCode` and `versionName` (Android, `android/app/build.gradle`) and the
  version and build (iOS, in Xcode) for every store upload.
- Geolocation needs a secure origin. Use `https://`, or for local testing on the
  Android emulator `http://localhost:3000` after `adb reverse tcp:3000 tcp:3000`.
