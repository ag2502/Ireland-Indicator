import { ImageResponse } from "next/og";

// The app icon is the arrival strip on a sign plate: a yellow range with the likely dot.

export function generateImageMetadata() {
  return [192, 512].map((n) => ({ id: String(n), size: { width: n, height: n }, contentType: "image/png" }));
}

export function Mark({ size }: { size: number }) {
  const u = size / 32;
  return (
    <div style={{ width: size, height: size, background: "#00553f", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div
        style={{
          width: size - 7 * u,
          height: size - 7 * u,
          border: `${1.6 * u}px solid #fff`,
          borderRadius: 3 * u,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <div style={{ position: "relative", width: 17 * u, height: 6 * u, background: "#ffd400", borderRadius: u, display: "flex" }}>
          <div
            style={{
              position: "absolute",
              left: 5.5 * u,
              top: 0.6 * u,
              width: 4.8 * u,
              height: 4.8 * u,
              borderRadius: 4 * u,
              background: "#000",
            }}
          />
          <div style={{ position: "absolute", left: 13 * u, top: -2 * u, width: 1.2 * u, height: 10 * u, background: "#fff" }} />
        </div>
      </div>
    </div>
  );
}

export default async function Icon({ id }: { id: Promise<string> }) {
  const n = Number(await id);
  return new ImageResponse(<Mark size={n} />, { width: n, height: n });
}
