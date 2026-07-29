import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on("error", (err: any) => {
            if (err?.code === "ECONNRESET" || err?.code === "ECONNABORTED") return;
            console.warn("[Vite Proxy Warning]", err.message);
          });
        }
      },
      "/ws": {
        target: "ws://127.0.0.1:8765",
        ws: true,
        configure: (proxy) => {
          proxy.on("error", (err: any) => {
            if (err?.code === "ECONNRESET" || err?.code === "ECONNABORTED") return;
            console.warn("[Vite WS Proxy Warning]", err.message);
          });
        }
      }
    }
  },
  build: {
    outDir: "dist",
    emptyOutDir: true
  }
});

