import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { loadEnv } from 'vite';
import { validateProductionApiUrl } from './src/buildConfig';

export default defineConfig(({ command, mode }) => {
  const apiUrl = command === 'build'
    ? validateProductionApiUrl(loadEnv(mode, '.', 'VITE_').VITE_API_BASE_URL)
    : undefined;
  return {
    ...(apiUrl ? { define: { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify(apiUrl) } } : {}),
    plugins: [react()],
    server: { port: 5173, strictPort: true, host: '127.0.0.1' },
    test: { environment: 'jsdom', setupFiles: './src/test/setup.ts' },
  };
});
