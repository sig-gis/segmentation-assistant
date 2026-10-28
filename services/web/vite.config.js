import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs on localhost:8000; /api is proxied to it so the
// browser sees one origin, exactly as behind nginx in Docker.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.API_URL || "http://localhost:8000",
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  build: { chunkSizeWarningLimit: 2000 }, // one bundle is fine for an internal tool
  test: { environment: "node" },
});
