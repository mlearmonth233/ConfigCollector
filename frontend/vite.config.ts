import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["favicon-16x16.png", "favicon-32x32.png", "apple-touch-icon.png"],
      manifest: {
        name: "ConfigCollector",
        short_name: "ConfigCollector",
        description: "Bulk network device config collection, scheduling, and backup",
        start_url: "/",
        display: "standalone",
        theme_color: "#2563eb",
        background_color: "#f5f6f8",
        icons: [
          { src: "pwa-192x192.png", sizes: "192x192", type: "image/png" },
          { src: "pwa-512x512.png", sizes: "512x512", type: "image/png" },
          { src: "pwa-512x512-maskable.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      // Precaches just the app shell (HTML/JS/CSS) so the installed window
      // opens instantly and offline - it does NOT cache API responses, so
      // devices/jobs/credentials always come from the live backend, never
      // stale cached data.
      workbox: {
        globPatterns: ["**/*.{js,css,html,svg,png,ico}"],
      },
    }),
  ],
  server: {
    port: 5173,
  },
});
