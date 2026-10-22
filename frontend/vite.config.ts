import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// /api is proxied to the FastAPI backend in dev (override with API_TARGET in
// frontend/.env.local); nginx does the same in the container.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  return {
    plugins: [react()],
    server: {
      proxy: {
        "/api": env.API_TARGET || "http://localhost:8000",
        "/health": env.API_TARGET || "http://localhost:8000",
      },
    },
  };
});
