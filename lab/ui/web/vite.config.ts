import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Build output lives outside the repo (and outside any folder named dist/ or data/).
const dataRoot = process.env.LAB_DATA_ROOT ?? "/home/chupa/Solana-project/data-old-faithful-one/lab";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: `${dataRoot}/ui-cache/web`,
    emptyOutDir: true,
    chunkSizeWarningLimit: 1200,
  },
});
