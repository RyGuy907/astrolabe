import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // PORT lets a launcher put it elsewhere when 5173 is taken.
    port: Number(process.env.PORT) || 5173,
    // The API runs separately on 8000; proxying keeps the browser same-origin.
    // ASTRO_API_URL overrides the target so a second instance can be run
    // against its own backend without disturbing one already on 8000.
    proxy: { "/api": process.env.ASTRO_API_URL ?? "http://127.0.0.1:8000" },
  },
});
