import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// El backend en dev vive en :8000. Para la demo móvil por túnel HTTPS, Vite
// proxya sus rutas: así el navegador solo habla con UN origen (el del túnel
// de 5173) y no choca con la página anti-phishing de devtunnels ni con CORS.
const BACKEND = "http://localhost:8000";
const BACKEND_ROUTES = [
  "/health", "/search", "/suggest", "/products", "/models3d",
  "/api", "/suppliers", "/recommend",
];

export default defineConfig({
  plugins: [react()],
  server: {
    // demo móvil vía túneles de VSCode (la AR real necesita HTTPS)
    allowedHosts: [".devtunnels.ms"],
    proxy: Object.fromEntries(BACKEND_ROUTES.map((r) => [r, BACKEND])),
  },
});
