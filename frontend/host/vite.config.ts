import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Node types are not loaded for this config file, so declare what the proxy handlers use.
type ProxyError = Error & { code?: string };
declare const console: { error: (...args: unknown[]) => void };

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  base: mode === 'production' ? '/host/' : '/',
  server: {
    // theme.css lives in frontend/, one level above this app; Vite blocks files outside its root by default.
    fs: { allow: ['..', '.'] },
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/socket.io': {
        target: 'http://localhost:8000',
        ws: true,
        changeOrigin: true,
        // A closed tab or a restarted backend breaks the socket mid-write. Hide that dev-only noise
        // (EPIPE, ECONNRESET) and still print every other proxy error.
        configure: (proxy) => {
          const ignore = (err: ProxyError) => err.code === 'EPIPE' || err.code === 'ECONNRESET';
          proxy.on('error', (err) => {
            if (!ignore(err)) console.error('[vite] ws proxy error:', err.message);
          });
          proxy.on('proxyReqWs', (_proxyReq, _req, socket) => {
            socket.on('error', (err: ProxyError) => {
              if (!ignore(err)) console.error('[vite] ws socket error:', err.message);
            });
          });
        },
      },
    },
  },
}));
