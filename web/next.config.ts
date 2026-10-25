import type { NextConfig } from "next";

// The browser only ever talks to this origin; /v1 and /healthz are proxied to the
// FastAPI backend, so there is no CORS and the API address stays server-side.
const api = process.env.BUSTRUST_API_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [
      { source: "/v1/:path*", destination: `${api}/v1/:path*` },
      { source: "/healthz", destination: `${api}/healthz` },
    ];
  },
  async headers() {
    return [
      {
        source: "/sw.js",
        headers: [
          { key: "Cache-Control", value: "no-cache" },
          { key: "Service-Worker-Allowed", value: "/" },
        ],
      },
    ];
  },
};

export default config;
