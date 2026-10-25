import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "BusTrust: Dublin buses, honestly",
    short_name: "BusTrust",
    description: "Unofficial Dublin bus times with an honest range, no-show risk and live status.",
    start_url: "/",
    display: "standalone",
    background_color: "#e7ebe9",
    theme_color: "#00553f",
    icons: [
      { src: "/icon/192", sizes: "192x192", type: "image/png" },
      { src: "/icon/512", sizes: "512x512", type: "image/png" },
      { src: "/icon/512", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
