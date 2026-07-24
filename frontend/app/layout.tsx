import type { Metadata, Viewport } from "next";
import { Bricolage_Grotesque, Hanken_Grotesk } from "next/font/google";
import "./globals.css";

// Characterful modern grotesque for display numerals + headings; clean grotesque for UI/body.
const display = Bricolage_Grotesque({
  subsets: ["latin"],
  variable: "--font-display-src",
  axes: ["opsz"],
  display: "swap",
});
const hanken = Hanken_Grotesk({
  subsets: ["latin"],
  variable: "--font-body-src",
  display: "swap",
});

export const metadata: Metadata = {
  title: "fettle",
  description: "Your health, read closely.",
  // Installed-to-home-screen mode (iOS): full-screen, no Safari chrome. The status
  // bar overlays the page (black-translucent) — globals.css paints an ink band under
  // it via env(safe-area-inset-top) so the clock stays legible in both themes.
  appleWebApp: { capable: true, statusBarStyle: "black-translucent", title: "fettle" },
  // Health numerals everywhere; don't let iOS turn digit runs into phone links.
  formatDetection: { telephone: false },
  // The home-screen icon rides plain http on the tailscale IP — the one fetch the
  // phone's webclip service has demonstrably completed (it rejects the proxy's
  // mkcert cert, and ts.net names failed it too). Served by the backend so every
  // fetch attempt lands in uvicorn's access log.
  icons: {
    // Declaring `icons` disables the app/-file auto-links, so the favicon must be
    // re-declared here (the file itself still serves at /icon.svg).
    icon: [{ url: "/icon.svg", type: "image/svg+xml" }],
    apple: [
      {
        url: "http://100.107.250.93:8400/api/icon/f.png",
        type: "image/png",
        sizes: "180x180",
      },
    ],
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Stops Safari's auto-zoom when focusing inputs (the coach composer). iOS still
  // honors a deliberate pinch, so accessibility zoom survives this.
  maximumScale: 1,
  // Extend the page under the notch/home indicator; safe-area insets pad it back.
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0a0b0d" },
    { media: "(prefers-color-scheme: light)", color: "#f3f1ea" },
  ],
};

// Resolve the theme (?theme= override, else saved choice, else system) before first
// paint — no flash of the wrong palette. Mirrors the toggle logic in the dashboard.
// The URL override is transient (never persisted): it exists for sharing/screenshots.
const themeScript =
  `(function(){try{var q=new URLSearchParams(location.search).get('theme');` +
  `var t=(q==='light'||q==='dark')?q:localStorage.getItem('theme');` +
  `var d=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';` +
  `document.documentElement.setAttribute('data-theme',(t==='light'||t==='dark')?t:d);}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={`${display.variable} ${hanken.variable}`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
