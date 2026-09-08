import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Während der Entwicklung Anfragen an /api an das Backend weiterleiten.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    chunkSizeWarningLimit: 1500,
  },
});
