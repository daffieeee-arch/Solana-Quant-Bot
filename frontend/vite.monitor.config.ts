import { fileURLToPath, URL } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  plugins: [react()],
  build: {
    outDir: 'dist-monitor',
    emptyOutDir: true,
    rollupOptions: { input: fileURLToPath(new URL('monitor.html', import.meta.url)) },
  },
});
