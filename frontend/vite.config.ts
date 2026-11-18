/// <reference types="vitest/config" />
// Vite build + dev server + Vitest configuration.
// `npm run dev` serves the UI on http://localhost:5173 and forwards /api to the FastAPI
// backend on port 8000. `npm run build` writes frontend/dist, which FastAPI then serves at
// http://127.0.0.1:8000 (one process for the demo).
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
  },
});
