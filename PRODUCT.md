# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Next.js (App Router, TypeScript) installable PWA in `web/`, reading the BusTrust FastAPI
backend at `/v1/*`. Live map with MapLibre GL. Chosen in the build plan.

## Users

A Dublin commuter at or near the stop, phone in one hand, deciding in seconds: is my bus
really coming, and when should I leave. The glance comes first and details second.
Planning-ahead use (route reliability, the leaderboard) is secondary.

## Product Purpose

An m-Indicator for Dublin buses. Beside every official arrival time it shows the honest
answer the official board does not: when the bus will probably come, an 80% range, and
the chance it never comes. It also scores each route's weekly reliability in public.
Success means riders wait less at the stop and miss fewer buses because they trusted a
countdown that lied.

## Positioning

Every number is graded in public. Predictions are logged live and checked each morning
against GPS-reconstructed arrivals, and the accuracy page shows the result, including
when we are wrong. Ghost-bus (no-show) risk per trip is something the official apps do
not show at all.

## Operating Context

- Used outdoors at stops, in rain and glare, often one-handed, on mid-range Android and iPhone.
- Network can be patchy, so the last board must stay readable offline with its age shown.
- Main path: home → nearby or searched stop → arrivals → (optional) leave-by for a walk time.
- Secondary: route page (7-day reliability, heatmap, slowest segments), leaderboard, live map, alerts, How it works.

## Capabilities and Constraints

- The official prediction is always shown **unaltered** beside ours (NTA fair usage policy).
- Nearby stops are computed **on the device** from a cached stop list; location never leaves the phone.
- Data is refreshed once a minute (one NTA call per minute). The UI must show data age and never imply second-level precision.
- Coverage v1: Dublin Bus and Go-Ahead Ireland Dublin routes.
- Luas, DART, Bikes and Fares tiles appear on the home grid marked **coming soon**: not live in v1, with no extra data sources.
- No accounts and no tracking in v1. Favourite stops live in local storage.

## Brand Commitments

- Name: **BusTrust**. Always marked **Unofficial**, with no operator or TFI logos, colours or names used as branding.
- Attribution on every data screen: "Contains Irish Public Sector Data (National Transport Authority) licensed under CC BY 4.0", plus Open-Meteo for weather.
- Voice: candid and plain. It says "this bus often doesn't show" outright, gives numbers with honest uncertainty, and uses no hype.

## Evidence on Hand

- Live accuracy numbers come from `/v1/accuracy`. Before real NTA data exists, the system runs on a simulator, so the UI must not present simulated figures as real-world results.
- No testimonials, press or user counts exist; do not invent any.

## Product Principles

1. The glance answers the question: likely time and risk are readable in under two seconds.
2. Show uncertainty honestly. A range rather than a single false-precision number, and a stale board labelled as stale.
3. Never hide or restyle the official number as if it were ours.
4. Privacy by construction: no location upload, no accounts.
5. Earn trust in public: the method and the scorecard are one tap away.

## Accessibility & Inclusion

WCAG 2.2 AA. Sunlight-readable contrast, risk never conveyed by colour alone, large tap
targets for one-handed use, works with screen readers and with system text scaling.
