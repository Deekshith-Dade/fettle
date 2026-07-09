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
  title: "fitbit+",
  description: "Your health, read closely.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0a0b0d" },
    { media: "(prefers-color-scheme: light)", color: "#f3f1ea" },
  ],
};

// Resolve the theme (saved choice, else system) before first paint — no flash of the
// wrong palette. Mirrors the toggle logic in the dashboard.
const themeScript =
  `(function(){try{var t=localStorage.getItem('theme');` +
  `var d=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';` +
  `document.documentElement.setAttribute('data-theme',(t==='light'||t==='dark')?t:d);}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${hanken.variable}`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
