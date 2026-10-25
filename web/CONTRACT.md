# web: contract

A Next.js PWA. The browser talks to one origin. In production Caddy sends `/v1/*` and
`/healthz` to the API. In development, `next.config.ts` rewrites them to
`BUSTRUST_API_URL`, which defaults to `http://127.0.0.1:8000`.

## Reads

| Endpoint | Used by | Polling |
|---|---|---|
| `/v1/stops/{id}/arrivals?walk_min=0` | stop page, favourites on home | every 30 s on the stop page, 60 s on home |
| `/v1/stops` (all stops) | nearby | once a day, then cached on the phone |
| `/v1/stops?q=` | home search | as the rider types, 200 ms debounce |
| `/v1/trips/{id}` | trip page | every 30 s |
| `/v1/vehicles`, `/v1/vehicles?route=` | map, route page | every 20 s on the map, 30 s on a route |
| `/v1/alerts` | alerts page, home tile | every 30 s on the page, 60 s on home |
| `/v1/routes/{r}/reliability`, `/v1/leaderboard`, `/v1/accuracy` | route, routes and how pages | every 10 min |
| `/v1/routes` | route search | every hour |
| `/healthz` | data notices on every page | every 60 s. A 503 is read, not treated as an error. |

Polling pauses while the tab is hidden and refreshes when it comes back.

## Stores on the device only

| Key | Holds |
|---|---|
| `bt:favs` | The rider's saved stops. |
| `bt:walk:<stop_id>` | Walking minutes to that stop, if the rider set them. |
| `bt:cache:<path>` | The last good response for each API path. It is shown, with its age, when the phone is offline. |
| Cache Storage (`sw.js`) | The app shell and static assets. Pages and `/v1` are network-first; `/_next/static` is cache-first. |

Location is used only to sort stops by distance, and the calculation runs on the phone.
It is never sent to the API.

## Rules for changes

- Show the official time unaltered and labelled "Official" (or "Timetable"), beside ours.
- In simulated mode (`feed_mode` from `/healthz`), every data page shows the orange notice.
- Colours, type and the sign system are in [DESIGN.md](DESIGN.md). Use the tokens on
  `:root`; don't hard-code colours.
