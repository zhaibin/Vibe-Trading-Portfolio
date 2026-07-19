import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const apiOrigin = "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: apiOrigin,
        changeOrigin: true,
        configure(proxy) {
          proxy.on("proxyReq", (proxyRequest) => {
            proxyRequest.setHeader("Origin", apiOrigin);
          });
        },
      },
    },
  },
  build: {
    outDir: "../src/vibe_portfolio/web/dist",
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      output: {
        hashCharacters: "hex",
        entryFileNames: "assets/[name]-[hash:16].js",
        chunkFileNames: "assets/[name]-[hash:16].js",
        assetFileNames: "assets/[name]-[hash:16][extname]",
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./vitest.setup.ts",
    coverage: {
      provider: "v8",
      reporter: ["text", "json-summary"],
      include: ["src/api/client.ts", "src/app/App.tsx", "src/pages/**/*.tsx"],
      thresholds: {
        lines: 80,
        functions: 80,
        statements: 80,
        branches: 75,
      },
    },
  },
});
