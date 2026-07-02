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
    // Tests must be independent of a developer's local .env.local. Pin the VAPID
    // public key to empty so the "not configured" default is deterministic even
    // when a real key is set locally for manual push testing.
    env: {
      VITE_VAPID_PUBLIC_KEY: '',
    },
  },
});
