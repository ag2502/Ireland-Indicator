"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";
import type { Health } from "@/lib/api";
import { ago } from "@/lib/format";
import { useNow, usePoll } from "@/lib/usePoll";
import * as Icon from "./icons";

const TABS = [
  { href: "/", label: "Home", icon: Icon.Home },
  { href: "/nearby", label: "Nearby", icon: Icon.Pin },
  { href: "/map", label: "Map", icon: Icon.MapIcon },
  { href: "/alerts", label: "Alerts", icon: Icon.Warn },
  { href: "/routes", label: "Routes", icon: Icon.Signpost },
];

export function TabBar() {
  const path = usePathname();
  const active = (href: string) =>
    href === "/" ? path === "/" || path.startsWith("/stop") : path.startsWith(href) || (href === "/routes" && path.startsWith("/route"));
  return (
    <nav className="tabs" aria-label="Main">
      <ul>
        {TABS.map(({ href, label, icon: Ic }) => (
          <li key={href}>
            <Link href={href} aria-current={active(href) ? "page" : undefined}>
              <Ic />
              {label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

/** The page header: a direction sign across the top of the screen. */
export function Head({ title, sub, back = true, action }: { title: ReactNode; sub?: ReactNode; back?: boolean; action?: ReactNode }) {
  const router = useRouter();
  return (
    <header className="plate head">
      {back && (
        <button
          className="head-btn"
          onClick={() => (history.length > 1 ? router.back() : router.push("/"))}
          aria-label="Back"
        >
          <Icon.Back />
        </button>
      )}
      <div className="head-main">
        <h1>{title}</h1>
        {sub && <p className="sub">{sub}</p>}
      </div>
      {action}
    </header>
  );
}

/**
 * Tells the rider what they are looking at: simulated numbers, an old board, no signal.
 * Shown on every data screen.
 */
export function DataNotices({ fetchedAt, offline, madeAt }: { fetchedAt?: number | null; offline?: boolean; madeAt?: number | null }) {
  const health = usePoll<Health>("/healthz", 60_000);
  const now = useNow();
  const age = madeAt ?? fetchedAt;
  return (
    <>
      {health.data?.feed_mode === "simulated" && (
        <p className="notice" role="note">
          <Icon.Warn />
          <span>
            Simulated data. These buses come from our test simulator, not the live NTA feed. <Link href="/how#simulated">Why</Link>
          </span>
        </p>
      )}
      {offline && (
        <p className="notice" role="status">
          <Icon.Offline />
          <span>No connection. Showing what this phone saved {ago(fetchedAt ?? null, now)}.</span>
        </p>
      )}
      {!offline && age != null && now - age > 180 && (
        <p className="notice" role="status">
          <Icon.Clock />
          <span>This board is {ago(age, now).replace(" ago", "")} old. Our feed may be down, so times may be out of date.</span>
        </p>
      )}
    </>
  );
}

export function Attribution({ text }: { text?: string }) {
  return (
    <footer className="footer">
      <p>
        {text ??
          "Unofficial. Not affiliated with Dublin Bus, Go-Ahead Ireland, the NTA or Transport for Ireland. Contains Irish Public Sector Data (National Transport Authority) licensed under CC BY 4.0."}
      </p>
      <p>
        <Link href="/how">How BusTrust works and how accurate it is</Link>
      </p>
    </footer>
  );
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading" style={{ display: "grid", gap: 10 }}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton" />
      ))}
    </div>
  );
}

export function Problem({ error, retry }: { error: Error; retry?: () => void }) {
  const notFound = "status" in error && (error as { status: number }).status === 404;
  return (
    <div className="empty" role="alert">
      <p>
        <b>{notFound ? "Not found." : "Could not load this."}</b> {notFound ? error.message : "Our server did not answer. Check your connection and try again."}
      </p>
      {retry && !notFound && (
        <button className="btn" onClick={retry}>
          Try again
        </button>
      )}
    </div>
  );
}
