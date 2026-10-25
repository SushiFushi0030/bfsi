import { defineConfig } from 'vite';

export default defineConfig({
  base: './',
  cacheDir: '.cache/vite',
  server: {
    host: '127.0.0.1', port: 5173, strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8787', changeOrigin: true,
        headers: { Origin: 'http://127.0.0.1:8787' },
      },
    },
  },
  preview: { host: '127.0.0.1', port: 4173, strictPort: true },
});
