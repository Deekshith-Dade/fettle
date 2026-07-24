import type { MetadataRoute } from "next";

// Installable-app manifest: lets "Add to Home Screen" open fettle standalone —
// its own icon and app-switcher card, no browser chrome. iOS takes the icon from
// app/apple-icon.png (auto-linked); these PNGs cover the manifest/Android side.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "fettle",
    short_name: "fettle",
    description: "Your health, read closely.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#0a0b0d",
    theme_color: "#0a0b0d",
    icons: [
      // Absolute http-IP URLs, same story as layout.tsx icons.apple: iOS may take
      // the tile from the manifest, and its fetcher chokes on the mkcert origin.
      { src: "http://100.107.250.93:8400/api/icon/f-192.png", sizes: "192x192", type: "image/png" },
      { src: "http://100.107.250.93:8400/api/icon/f-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
    ],
  };
}
