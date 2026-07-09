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
  themeColor: "#0a0b0d",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${hanken.variable}`}>
      <body>{children}</body>
    </html>
  );
}
