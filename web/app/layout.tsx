import type { Metadata, Viewport } from "next";
import { Atkinson_Hyperlegible_Next, Barlow_Condensed } from "next/font/google";
import { TabBar } from "@/components/chrome";
import { RegisterSW } from "@/components/RegisterSW";
import "./globals.css";

const barlow = Barlow_Condensed({ subsets: ["latin", "latin-ext"], weight: ["600", "700"], variable: "--font-barlow" });
const atkinson = Atkinson_Hyperlegible_Next({ subsets: ["latin", "latin-ext"], variable: "--font-atkinson" });

export const metadata: Metadata = {
  title: { default: "BusTrust: is my Dublin bus really coming?", template: "%s · BusTrust" },
  description:
    "Unofficial Dublin bus times with an honest range, the chance a bus never shows, live status and public reliability scores.",
  applicationName: "BusTrust",
  appleWebApp: { capable: true, title: "BusTrust", statusBarStyle: "black-translucent" },
  formatDetection: { telephone: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#00553f",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-IE" className={`${barlow.variable} ${atkinson.variable}`}>
      <body>
        {children}
        <TabBar />
        <RegisterSW />
      </body>
    </html>
  );
}
