import { fileURLToPath, URL } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  plugins: [react()],
  build: { outDir: 'dist-inspector', emptyOutDir: true,
    rollupOptions: { input: fileURLToPath(new URL('inspector.html', import.meta.url)) } },
});
