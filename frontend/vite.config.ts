import { readFileSync } from "node:fs";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Version lue depuis package.json par le systeme de fichiers (pas un import JSON, qui
// exigerait resolveJsonModule dans le tsconfig du build). Le SHA vient de la CI ; en local,
// "dev" -- on ne pretend pas connaitre un commit qu'on n'a pas.
const pkg = JSON.parse(
  readFileSync(new URL("./package.json", import.meta.url), "utf-8"),
) as { version: string };

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, host: true },
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
    __BUILD_SHA__: JSON.stringify(process.env.VITE_BUILD_SHA || "dev"),
  },
});
