import { fileURLToPath, URL } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true },
  preview: {
    host: '192.168.1.234',
    port: 3100,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://192.168.1.234:3000', changeOrigin: true },
    },
  },
});
