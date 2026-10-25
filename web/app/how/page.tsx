"use client";

import { Attribution, DataNotices, Head } from "@/components/chrome";
import type { Accuracy, Health } from "@/lib/api";
import { pct, secs } from "@/lib/format";
import { usePoll } from "@/lib/usePoll";

export default function How() {
  const acc = usePoll<Accuracy>("/v1/accuracy?days=30", 600_000);
  const health = usePoll<Health>("/healthz", 60_000);
  const s = acc.data?.summary;
  const simulated = health.data?.feed_mode === "simulated";

  return (
    <main className="page">
      <Head title="How BusTrust works" sub="What the numbers mean, where they come from, and how often we are wrong." />
      <DataNotices />

      <section className="section" aria-labelledby="acc-h">
        <h2 className="section-title" id="acc-h">
          Our scorecard, last 30 days
        </h2>
        {s ? (
          <>
            <div className="figures">
              <div className="figure">
                <p className="n">{secs(s.mae_ours_s)}</p>
                <p>our typical miss on arrival time</p>
              </div>
              <div className="figure">
                <p className="n">{secs(s.mae_official_s)}</p>
                <p>the official prediction&apos;s typical miss, same buses</p>
              </div>
              <div className="figure">
                <p className="n">{pct(s.coverage * 100)}</p>
                <p>of buses arrived inside our range (we aim for 80%)</p>
              </div>
              <div className="figure">
                <p className="n">{s.n.toLocaleString("en-IE")}</p>
                <p>predictions checked against GPS</p>
              </div>
            </div>
            <p className="muted small" style={{ marginTop: 8 }}>
              Typical miss is the mean absolute error.
              {simulated && " These figures come from simulated buses, so they say the method works, not how it does on Dublin's streets."}
            </p>
          </>
        ) : (
          <div className="empty">
            <p>
              <b>No graded days yet.</b> Every morning we check yesterday&apos;s predictions against where the buses actually were. The first
              results appear the day after our predictions go live.
            </p>
          </div>
        )}
      </section>

      <article className="section prose">
        <h2>What you see at a stop</h2>
        <ul>
          <li>
            <b>Official</b> is the time from the National Transport Authority&apos;s live feed, shown exactly as published. <b>Timetable</b> means
            the bus is not in the live feed yet.
          </li>
          <li>
            <b>Likely</b> is our estimate of when the bus will actually arrive. The yellow band is the range we expect 8 in 10 buses to arrive
            in. A wide band is us being honest that this one is hard to call.
          </li>
          <li>
            <b>Chance it won&apos;t come</b> is how likely a scheduled bus is never to show up at all, which happens more often than the
            official boards admit. We only mention it when it is worth your attention.
          </li>
          <li>
            <b>Status</b> says what the bus is doing right now: on time, late or early (from its GPS, or the feed when it has none), cancelled,
            diverted away from your stop, or already gone. Buses that left in the last 15 minutes stay on the board, so you know you missed it
            rather than wondering.
          </li>
        </ul>

        <h2>How the predictions are made</h2>
        <p>
          Once a minute we read the NTA&apos;s live feed of predictions and bus positions, the only call we make to it. From the GPS trail we
          work out when each bus really reached each stop. A model learns how the official prediction tends to be wrong, given things like how
          far away the bus is, how late the buses ahead are running, the time of day and the weather. It is retrained every week on recent
          weeks and only replaces the old one if it does better on days it has never seen.
        </p>
        <p>
          The no-show chance comes from a second model that looks at whether a bus has started its trip, how late the previous trip of the
          same vehicle is running, and how often that route&apos;s buses failed to show in the past week.
        </p>

        <h2>Scores in public</h2>
        <p>
          Each morning every route is scored on the day before: did the scheduled trips run, did they keep time, did they bunch. The rankings
          use the same GPS data for every route and are never edited by hand.
        </p>

        <h2 id="simulated">Simulated data</h2>
        <p>
          Until BusTrust is connected to the NTA&apos;s live feed, it runs on a simulator that produces realistic buses with delays,
          cancellations, diversions and GPS gaps. Every screen says so while that is the case. Simulated numbers show the system works end to
          end; they are not a measure of Dublin&apos;s buses.
        </p>

        <h2>Your privacy</h2>
        <p>
          No accounts and no tracking. Stops near you are worked out on your phone from a list of every stop it downloads once a day, so your
          location never reaches us. Saved stops and walk times stay on your phone.
        </p>

        <h2>Who made this</h2>
        <p>
          BusTrust is an independent, unofficial project. It is not affiliated with Dublin Bus, Go-Ahead Ireland, the National Transport
          Authority or Transport for Ireland, and uses none of their branding.
        </p>
        <h2>Data and credits</h2>
        <ul>
          <li>Contains Irish Public Sector Data (National Transport Authority) licensed under a Creative Commons Attribution 4.0 licence.</li>
          <li>Weather from Open-Meteo.com, licensed under CC BY 4.0.</li>
          <li>Map data © OpenStreetMap contributors, map tiles by OpenFreeMap.</li>
        </ul>
      </article>
      <Attribution />
    </main>
  );
}
