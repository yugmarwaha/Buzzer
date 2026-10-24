import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  base: mode === 'production' ? '/admin/' : '/',
  server: {
    // theme.css lives in frontend/, one level above this app; Vite blocks files outside its root by default.
    fs: { allow: ['..', '.'] },
    port: 5175,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
}));
