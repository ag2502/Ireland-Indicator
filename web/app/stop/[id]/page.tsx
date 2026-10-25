"use client";

import { use } from "react";
import { ArrivalBoard } from "@/components/ArrivalBoard";
import { Attribution, DataNotices, Head, Problem, Skeleton } from "@/components/chrome";
import * as Icon from "@/components/icons";
import type { Arrivals } from "@/lib/api";
import { useFavs, useWalk } from "@/lib/device";
import { ago } from "@/lib/format";
import { useNow, usePoll } from "@/lib/usePoll";

export default function StopPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const stopId = decodeURIComponent(id);
  // walk 0 gives the early edge as leave_by_at; the rider's walk time is applied on the
  // phone, so the walk never leaves the device and the response stays cacheable
  const board = usePoll<Arrivals>(`/v1/stops/${encodeURIComponent(stopId)}/arrivals?walk_min=0`, 30_000);
  const now = useNow();
  const [walk, setWalk] = useWalk(stopId);
  const { has, toggle } = useFavs();
  const stop = board.data?.stop;
  const fav = stop ? has(stop.stop_id) : false;

  // the API's leave_by_at is for walk 0: shift it by the rider's own walk time here
  const arrivals = (board.data?.arrivals ?? []).map((a) => ({
    ...a,
    leave_by_at: a.leave_by_at != null && walk != null ? a.leave_by_at - walk * 60 : null,
  }));

  return (
    <main className="page">
      <Head
        title={stop?.stop_name ?? (board.error ? "Stop" : "Loading stop…")}
        sub={
          stop && (
            <>
              {stop.stop_code ? `Stop ${stop.stop_code}. ` : ""}
              {board.data?.board_made_at ? `Updated ${ago(board.data.board_made_at, now)}` : ""}
            </>
          )
        }
        action={
          stop && (
            <button
              className="head-btn"
              aria-pressed={fav}
              aria-label={fav ? "Remove from your stops" : "Save to your stops"}
              onClick={() => toggle({ stop_id: stop.stop_id, name: stop.stop_name ?? stop.stop_id, code: stop.stop_code })}
            >
              <Icon.Star filled={fav} />
            </button>
          )
        }
      />
      <DataNotices fetchedAt={board.fetchedAt} offline={board.offline} madeAt={board.data?.board_made_at} />

      <div className="walk">
        <p className="walk-label">
          {walk == null ? "Add your walk time to see when to leave" : "Your walk to this stop"}
        </p>
        <div className="stepper" role="group" aria-label="Walk time">
          <button aria-label="Less walking time" onClick={() => setWalk(walk == null || walk <= 1 ? null : walk - 1)}>
            −
          </button>
          <output aria-live="polite">{walk == null ? "Off" : `${walk} min`}</output>
          <button aria-label="More walking time" onClick={() => setWalk(Math.min((walk ?? 0) + 1, 60))}>
            +
          </button>
        </div>
      </div>

      <section className="section" aria-label="Buses at this stop">
        {board.data ? (
          <ArrivalBoard arrivals={arrivals} now={now} walkMin={walk} />
        ) : board.error ? (
          <Problem error={board.error} retry={board.refresh} />
        ) : (
          <Skeleton />
        )}
      </section>

      <Attribution text={board.data?.attribution} />
    </main>
  );
}
