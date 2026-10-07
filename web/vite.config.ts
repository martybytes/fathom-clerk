import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// Dev: vite serves the SPA on 5173 and proxies the API to the Python server, so
// the page is same-origin in development exactly as it is in production.
// Prod: `vite build` emits dist/, which fath/server/app.py serves itself, with
// the token spliced into index.html at serve time.
//
// base is relative so the bundle does not care what path it is mounted at.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // The bundle is committed so `fath web` needs no node. Keeping the
    // filenames stable means a rebuild produces a readable diff instead of a
    // wall of renamed hashes.
    rollupOptions: {
      output: {
        entryFileNames: "assets/app.js",
        chunkFileNames: "assets/[name].js",
        assetFileNames: "assets/[name][extname]",
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8899",
      "/healthz": "http://127.0.0.1:8899",
    },
  },
});
