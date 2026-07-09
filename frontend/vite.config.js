import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';

// PUBLIC DEMO build: VITE_DEMO=1 swaps the network layer (src/api.js) for the
// synthetic in-memory one (src/apiDemo.js) at resolve time, so every importer
// picks it up with zero source changes. Normal builds and vitest keep an empty
// alias list (tests inject their own fakes and never rely on this).
const DEMO = process.env.VITE_DEMO === '1';

// The demo is served from a SUBPATH of the portfolio site (…/demo/), so its
// HTML must reference every asset relatively. Vite's base './' covers bundled
// assets; this plugin additionally rewrites root-absolute public/ references
// in index.html (e.g. /commbank-mark.svg) and drops the PWA manifest link —
// the demo is not installable and the manifest's absolute paths would 404.
function demoHtmlRelativizer() {
  return {
    name: 'demo-html-relativizer',
    transformIndexHtml(html) {
      if (!DEMO) return html;
      return html
        .replace(/^\s*<link rel="manifest"[^>]*>\s*\r?\n/m, '')
        .replace(/(src|href)="\/(?!\/)/g, '$1="./');
    },
  };
}

export default defineConfig({
  base: DEMO ? './' : '/',
  plugins: [demoHtmlRelativizer()],
  resolve: {
    alias: DEMO
      ? [
          {
            find: /^\.\/api\.js$/,
            replacement: fileURLToPath(new URL('./src/apiDemo.js', import.meta.url)),
          },
        ]
      : [],
  },
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
