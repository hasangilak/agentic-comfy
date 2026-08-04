import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// In development the API and the rendered media live on the studio server; everything
// else is served by Vite. In production `npm run build` writes dist/, which studio.py
// mounts at /.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Pinned to IPv4. Left to itself Vite binds ::1 only, so http://127.0.0.1:5173 refuses
    // the connection while http://localhost:5173 works -- a confusing way to lose ten minutes.
    host: "127.0.0.1",
    proxy: {
      "/api": { target: "http://127.0.0.1:8787", changeOrigin: true },
      "/media": { target: "http://127.0.0.1:8787", changeOrigin: true },
    },
  },
});
