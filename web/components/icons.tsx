import type { SVGProps } from "react";

type P = SVGProps<SVGSVGElement>;

function I({ children, ...p }: P) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...p}>
      {children}
    </svg>
  );
}

export const Home = (p: P) => (
  <I {...p}>
    <path d="M3 11 12 4l9 7" />
    <path d="M5 10v10h5v-6h4v6h5V10" />
  </I>
);

export const Pin = (p: P) => (
  <I {...p}>
    <path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21Z" />
    <circle cx="12" cy="9.5" r="2.5" />
  </I>
);

export const MapIcon = (p: P) => (
  <I {...p}>
    <path d="M9 4 3 6.5v13.5l6-2.5 6 2.5 6-2.5V4l-6 2.5L9 4Z" />
    <path d="M9 4v13.5M15 6.5V20" />
  </I>
);

export const Warn = (p: P) => (
  <I {...p}>
    <path d="M12 3 2 20h20L12 3Z" />
    <path d="M12 10v4.5M12 17.5v.01" />
  </I>
);

export const Signpost = (p: P) => (
  <I {...p}>
    <path d="M12 3v18" />
    <path d="M5 6h12l2.5 2.5L17 11H5V6ZM19 13H7l-2.5 2.5L7 18h12v-5Z" />
  </I>
);

export const Star = ({ filled, ...p }: P & { filled?: boolean }) => (
  <I {...p} fill={filled ? "currentColor" : "none"}>
    <path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9L12 3Z" />
  </I>
);

export const Back = (p: P) => (
  <I {...p}>
    <path d="M15 5 8 12l7 7" />
  </I>
);

export const Chevron = (p: P) => (
  <I {...p}>
    <path d="m9 5 7 7-7 7" />
  </I>
);

export const Search = (p: P) => (
  <I {...p}>
    <circle cx="11" cy="11" r="6.5" />
    <path d="m20 20-4.2-4.2" />
  </I>
);

export const Bus = (p: P) => (
  <I {...p}>
    <rect x="4" y="3" width="16" height="15" rx="2.5" />
    <path d="M4 11h16M8 21v-3M16 21v-3" />
    <circle cx="8" cy="14.5" r=".6" fill="currentColor" />
    <circle cx="16" cy="14.5" r=".6" fill="currentColor" />
  </I>
);

export const Tram = (p: P) => (
  <I {...p}>
    <rect x="5" y="6" width="14" height="12" rx="3" />
    <path d="M9 2h6M12 2v4M5 13h14M8 21l1.5-3M16 21l-1.5-3" />
  </I>
);

export const Train = (p: P) => (
  <I {...p}>
    <path d="M6 4h12a2 2 0 0 1 2 2v9a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3V6a2 2 0 0 1 2-2Z" />
    <path d="M4 11h16M8 21l2-3M16 21l-2-3" />
  </I>
);

export const Bike = (p: P) => (
  <I {...p}>
    <circle cx="6" cy="16" r="3.5" />
    <circle cx="18" cy="16" r="3.5" />
    <path d="M6 16 9.5 8H14l4 8M9.5 8 12 16h-6M13 5h2.5" />
  </I>
);

export const Ticket = (p: P) => (
  <I {...p}>
    <path d="M3 7h18v3a2 2 0 0 0 0 4v3H3v-3a2 2 0 0 0 0-4V7Z" />
    <path d="M14 7v10" strokeDasharray="2 2" />
  </I>
);

export const Info = (p: P) => (
  <I {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 11v6M12 7.5v.01" />
  </I>
);

export const Cross = (p: P) => (
  <I {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="m9 9 6 6M15 9l-6 6" />
  </I>
);

export const Detour = (p: P) => (
  <I {...p}>
    <path d="M5 20V9a4 4 0 0 1 4-4h8" />
    <path d="m14 2 3 3-3 3M19 20v-6" />
  </I>
);

export const Gps = (p: P) => (
  <I {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
    <circle cx="12" cy="12" r="7" />
  </I>
);

export const Clock = (p: P) => (
  <I {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3 2" />
  </I>
);

export const Gone = (p: P) => (
  <I {...p}>
    <path d="M4 12h12M12 6l6 6-6 6" />
    <path d="M21 4v16" />
  </I>
);

export const Offline = (p: P) => (
  <I {...p}>
    <path d="M2 8.5a15 15 0 0 1 4-2.4M22 8.5A15 15 0 0 0 10 5.2M5 12.5a10 10 0 0 1 3.5-2M19 12.5a10 10 0 0 0-4.2-2.3M8.5 16a5 5 0 0 1 7 0M12 20v.01M3 3l18 18" />
  </I>
);
