import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    host: true,
    // Allow the dev server to be reached over the Tailscale tailnet (MagicDNS
    // names end in .ts.net). Wildcard leaf-dot form, so no specific machine
    // name is hardcoded here. The dev server is only reachable via localhost or
    // the private tailnet, so this is safe.
    allowedHosts: ['.ts.net'],
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.test.js'],
  },
});
