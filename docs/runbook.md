# Runbook

How to stand BusTrust up, keep it healthy, and fix it when something breaks. All
commands run from the repo root on the server unless a step says otherwise. Every
`compose` below is short for:

```bash
docker compose --env-file .env -f infra/docker-compose.yml
```

## First deploy

1. **Server.** Any Linux VM with 2 vCPU, 4 GB of RAM and 40 GB of disk. Install Docker
   Engine and the compose plugin. Open ports 80 and 443.
2. **DNS.** Point an A (and AAAA) record for your domain at the server.
3. **Code and settings.**
   ```bash
   sudo git clone https://github.com/ag2502/Ireland-Indicator.git /opt/bustrust
   cd /opt/bustrust && sudo cp .env.example .env
   ```
   Edit `.env`:
   - `POSTGRES_PASSWORD`: a long random string.
   - `BT_DOMAIN`: your domain.
   - `BT_FEED_MODE`: keep `simulated` until you have an NTA key (see below).
   - Optional: `BT_HEARTBEAT_URL` (a Healthchecks.io ping URL), and
     `BT_RAW_ARCHIVE=s3://…` with `AWS_*` credentials.
4. **Start.**
   ```bash
   compose up -d --build
   ```
   `migrate` creates the tables and loads the schedule, which takes a few minutes the
   first time. The poller, scorer, jobs and API start after it finishes. Caddy gets the
   TLS certificate on the first request.
5. **Check.** Open `https://<domain>/healthz`. A 200 response with `"feed_mode"` means
   it is serving.
6. **Optional: demo history.** For the reliability and accuracy pages to have
   something to show in simulated mode:
   ```bash
   compose exec jobs bustrust simulate-backfill --days 14
   compose exec jobs sh -c 'bustrust build-labels --days 14 && bustrust features --days 14 && bustrust scores --days 14 && bustrust train'
   ```

### Automatic deploys from GitHub

Add these repository secrets: `DEPLOY_HOST`, `DEPLOY_USER` (a user in the `docker` group
that can write to `/opt/bustrust`), `DEPLOY_KEY` (a private SSH key for that user) and
`DEPLOY_KNOWN_HOSTS`. Fill `DEPLOY_KNOWN_HOSTS` with the output of
`ssh-keyscan <host>`, compared by hand against the server's own key. Set `DEPLOY_PATH`
only if the clone is somewhere other than `/opt/bustrust`.

Then add a repository **variable** (Settings → Secrets and variables → Actions →
Variables) named `DEPLOY_ENABLED` with the value `true`. From then on, every push to
`main` that passes CI is deployed, and the weekly report workflow commits
`docs/evaluation.json` each Monday. Until that variable is set, both workflows show as
skipped.

## Mobile apps

The Android and iOS apps in `mobile/` load the live site, so the site must be deployed
on `https://` first. After that, web deploys reach the apps without a store release.
Build the apps again only when `mobile/` changes. All commands run in `mobile/` on a Mac.

```bash
npm ci
BUSTRUST_APP_URL=https://<domain> npx cap sync
```

### Google Play (one-off fee: $25)

1. **Upload key, once.** Keep the `.jks` file and its password somewhere safe outside
   the repo, such as a password manager:
   ```bash
   keytool -genkeypair -keystore ~/bustrust-upload.jks -alias upload -keyalg RSA -keysize 2048 -validity 10000
   ```
   Then create `android/keystore.properties` (gitignored):
   ```
   storeFile=/Users/<you>/bustrust-upload.jks
   storePassword=…
   keyAlias=upload
   keyPassword=…
   ```
2. **Build.** Raise `versionCode` (by 1) and `versionName` in `android/app/build.gradle`,
   then run `cd android && ./gradlew bundleRelease`. The upload file is
   `android/app/build/outputs/bundle/release/app-release.aab`. The build needs
   JDK 21; Android Studio includes one.
3. **Play Console.** Create the app and turn on Play App Signing, which lets Google reset
   a lost upload key. Fill in the store listing, the content rating, and the data safety
   form: the app collects nothing, and location stays on the device. Link a privacy
   policy URL. New personal accounts must run a closed test with at least 12 testers for
   14 days before production.

A signed `app-release.apk` (`./gradlew assembleRelease`) can also be shared directly,
for example on a GitHub release. Android users then have to allow installs from
unknown sources.

### Apple App Store (fee: $99 a year)

1. Install Xcode from the Mac App Store and enrol in the Apple Developer Program.
2. Run `npx cap open ios`. In Signing & Capabilities, choose your team. Raise the version
   and build number, then run Product → Archive and upload from the Organizer.
3. In App Store Connect, add the listing, screenshots, the privacy label (no data
   collected) and the privacy policy URL, then submit for review.

Apple rejects apps that are "just a website" (guideline 4.2). When you submit, point to
what works like an app: offline arrivals from the last good response, location-based
nearby stops, and saved stops. Adding push alerts for saved stops would make approval
more likely.

### Testing on the Android emulator

```bash
adb reverse tcp:3000 tcp:3000          # the emulator's localhost reaches `npm run dev`
BUSTRUST_APP_URL=http://localhost:3000 npx cap sync android
cd android && ./gradlew installDebug
```

Use `localhost`, not `10.0.2.2`: location only works on a secure origin, and
`localhost` counts as one.

## Switching to the real NTA feed

1. Register at the NTA developer portal and subscribe to GTFS-Realtime. You get a primary
   and a secondary key.
2. In `.env` set `BT_FEED_MODE=nta` and `BT_NTA_API_KEYS=<primary>`. Then run
   `compose up -d poller scorer`.
3. Keep the simulated history out of training. Either start on a fresh database
   (`compose down -v` deletes **all** data, so only do this before real data exists), or
   train only on days after the switch.
4. **Two keys:** the fair usage policy allows one realtime call per key per minute.
   Using both keys at once (each feed refreshing every minute instead of every two) needs
   written agreement first. Email apisupport@nationaltransport.ie. Only after they agree,
   add the second key to `BT_NTA_API_KEYS` and set `BT_USE_BOTH_KEYS=true`.

## Daily checks (two minutes)

- `compose ps`: every service is `running`, and `migrate` exited 0.
- `compose logs --since 24h jobs | grep -E "FAIL|Error|Traceback"`: this should print nothing.
- `https://<domain>/how`: yesterday's accuracy row exists.

## Weekly (Monday)

- Read the retrain result: `compose logs --since 12h jobs | grep -E "PROMOTED|kept old"`,
  or the new `docs/evaluation.json` commit.
- Before trusting a newly promoted arrival model, compare its coverage by horizon with
  last week's. Coverage that drifts by more than 3 points between weeks means the
  calibration days were unusual.
- Check `calendar_ie.csv` for upcoming bank holidays, school breaks and big events at
  Croke Park or the Aviva.

## When something breaks

### /healthz returns 503, or the app shows "data is X minutes old"

1. Check the poller: `compose logs --tail 50 poller`.
   - **429 responses:** we are over the fair usage limit. Check that only one poller is
     running (`compose ps`) and that `BT_USE_BOTH_KEYS` is false unless the NTA agreed.
     The poller backs off by itself, up to 15 minutes.
   - **401 or 403:** the key is wrong or expired. Fix `.env`, then run `compose up -d poller`.
   - **5xx errors or timeouts:** the NTA is down. Nothing to do. The gap is recorded,
     labels skip it, and the app shows its stale-data notice.
2. If the poller is fine, check the scorer: `compose logs --tail 50 scorer`. A traceback
   here usually means bad data in the latest poll. Restart it with `compose restart scorer`.
   If that doesn't help, open an issue with the log.

### The nightly checks failed

Run `compose exec jobs bustrust checks` to see which check failed and its value.

| Check | Usual cause | Fix |
|---|---|---|
| `poll_success`, `max_poll_gap` | NTA outage or a poller crash | See above. Past gaps need no action. |
| `schedule_join` | The NTA issued a new static zip and the old one is still loaded | `compose exec jobs bustrust load-schedule`, then rebuild the day: `bustrust build-labels --days 1` |
| `gps_coverage` | The vehicles feed was missing for hours | Usually fixes itself. If it persists, check that the poller is fetching both feeds. |
| `impossible_speed`, `stop_order` | Bad GPS from a few buses | Nothing, unless it climbs for days. Then look at which vehicles are involved. |

### A bad model was promoted

The registry is `BT_MODEL_DIR/registry.json`. Under `current`, set the kind back to the
previous entry from `history`, copying its `version` and `file`. The scorer rereads the
registry every 10 minutes, so no restart is needed. To stop showing a range at all,
delete `current.arrival`. The app then shows official times only and says so.

### Disk is filling up

- Postgres: Timescale compresses the realtime tables after 7 days. Check sizes with
  `compose exec db psql -U bustrust -c "SELECT hypertable_name, pg_size_pretty(hypertable_size(format('%I', hypertable_name)::regclass)) FROM timescaledb_information.hypertables"`.
- Raw archive: about 1 GB a month gzipped. Move it to S3 with `BT_RAW_ARCHIVE=s3://…`.
- Docker: `docker image prune -f` and `docker builder prune -f`.

## Backups

There are two layers of backup.

**The raw and static archives** keep every feed response and every schedule zip. If the
database is lost, everything can be rebuilt except `predictions_log`, `accuracy_daily`
and `poll_log`/`feed_gaps`:

```bash
compose exec jobs bustrust init-db
compose exec jobs bustrust load-schedule --zip /data/static/<schedule_version>.zip   # each version in use
compose exec jobs bustrust replay-raw --start 2026-11-01 --end 2026-11-30            # UTC days
compose exec jobs sh -c 'bustrust build-labels --days 30 && bustrust features --days 30 && bustrust scores --days 30'
```

Keep both archives on S3 with versioning turned on.

**A nightly database dump** covers the tables the archives can't rebuild. Run it on
the host, not in the container:

```bash
0 3 * * * cd /opt/bustrust && docker compose --env-file .env -f infra/docker-compose.yml exec -T db pg_dump -U bustrust -Fc bustrust > /var/backups/bustrust-$(date +\%a).dump
```

That keeps a rolling week of dumps. To restore one:
```bash
compose exec -T db pg_restore -U bustrust -d bustrust --clean < /var/backups/bustrust-Mon.dump
```

## Local development

```bash
uv sync
./scripts/fix_macos_libomp.sh          # macOS only
cp .env.example .env                   # SQLite and simulated feed by default
.venv/bin/bustrust init-db && .venv/bin/bustrust load-schedule
.venv/bin/bustrust poll &              # simulated feed
.venv/bin/bustrust score &
.venv/bin/python -m uvicorn api.main:app --port 8000 &
cd web && npm install && npm run dev   # http://localhost:3000
```
