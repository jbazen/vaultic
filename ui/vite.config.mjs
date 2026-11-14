import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8001",
    },
  },
  build: {
    rolldownOptions: {
      output: {
        // Vendor chunks that change rarely, so browsers keep them cached across deploys.
        codeSplitting: {
          groups: [
            { name: "react", test: /node_modules[\/](react|react-dom|scheduler)[\/]/, priority: 20 },
            { name: "recharts", test: /node_modules[\/](recharts|victory-vendor|d3-[^\/]+)[\/]/, priority: 10 },
          ],
        },
      },
    },
  },
});
