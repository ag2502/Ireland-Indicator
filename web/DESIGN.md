# BusTrust web: design plan

Method: Anthropic's frontend-design guidance (plan, check against generic defaults,
build, critique), with the UI UX Pro Max checklist for transit and real-time
monitoring apps. Product context lives in `../PRODUCT.md`.

## The idea

The rider is standing at a Dublin bus stop, so the app borrows what the street
already teaches them to read: **Irish road signs**. Green direction plates with an
inset white keyline and white condensed lettering for the things you navigate by
(stops, routes, the home tiles). Warning-sign yellow for the one thing the official
board never shows: uncertainty. Regulatory red only for "this bus is not coming".

The one memorable thing is the **arrival strip**: every bus at a stop sits on the same
minute scale, so you see at a glance which comes first, how wide its range is, and
where the official time falls inside or outside it.

## Colour

| Token | Hex | Use |
|---|---|---|
| `--sign` | `#00553F` | road-sign green: plates, header, route numbers (white on it is 8.9:1) |
| `--warn` | `#FFD400` | warning-sign yellow: the likely range band, black text only |
| `--stop` | `#C8102E` | regulatory red: cancelled, not stopping here |
| `--ground` | `#E7EBE9` | kerb grey page |
| `--paper` | `#FFFFFF` | rows and panels |
| `--ink` | `#000000` | text, the official-time notch |

Dark mode keeps the plates and turns the ground to deep sign green (`#05261C`), like a
sign under headlights. Contrast is checked for both.

## Type

- **Barlow Condensed** (600, 700): route numbers, minutes, stop names on plates.
  Condensed figures fit a big countdown and a route number on a 360 px phone.
- **Atkinson Hyperlegible Next**: everything else. Built for low-vision readers,
  which is the right default for a phone read in rain and glare.
- Sentence case everywhere. No all-caps labels, no monospace.

## Layout

```
Stop page (phone)
┌────────────────────────────────┐
│▐ Westmoreland Street        ★  │  green plate, keyline, stop code
│▐ Stop 315 · updated just now   │
├────────────────────────────────┤
│ Walk  [−] 4 min [+]            │  leave-by control, remembered per stop
├────────────────────────────────┤
│ now  5   10   20   30   45  60 │  shared scale (square-root: near minutes get room)
│[39A] Ongar               12 min│
│      ░░▓▓▓●▓▓░|          10–15 │  yellow range, ● likely, | official
│      4 min late · 3 stops away │
│[46A] Dún Laoghaire   Cancelled │  red plate, words, struck time
│[145] Heuston        Left 2 min │  gone section, muted
└────────────────────────────────┘
  Home  Nearby  Map  Alerts  Routes   bottom tabs, 56 px
```

Left-aligned throughout. Lists are rows on one surface, not a card per bus.

## Principles

1. The glance answers it: likely minutes and status readable in two seconds.
2. Official stays official: shown unaltered and labelled, never restyled as ours.
3. Status in words and shape, never colour alone (struck time, plate colour, label).
4. Minutes, never seconds. Data age is always visible; a stale board says so.
5. Simulated data is labelled as simulated on every screen until the NTA feed is live.
6. 44 px targets, 4.5:1 contrast, visible focus, reduced motion respected.

## What was revised after checking against defaults

- First idea was Dublin's amber LED dot-matrix display on black. Rejected: it is the
  near-black-plus-one-accent default, and it washes out in sunlight.
- Home tiles were going to be a uniform grid of rounded cards. Now they are sign
  plates of different sizes by importance, and the coming-soon ones are drawn as
  bagged (covered) signs, as on a road that is not open yet.
- Dropped a monospace face for times; Barlow Condensed figures do that job.
