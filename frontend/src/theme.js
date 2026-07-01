/**
 * theme.js — light/dark theme tokens + thin DOM binding.
 * Pure token logic (THEME_TOKENS, nextTheme) plus small, testable DOM helpers
 * (applyTheme, initTheme). No network, no secrets. Values are copied verbatim
 * from the design mockup's themeTokens() (oklch design tokens only — no
 * transaction data).
 */

/** Design accent — same for both themes (mockup default accentColor). */
export const ACCENT = '#3d9a6f';

/** Theme token sets, keyed by theme name. Values are oklch() strings. */
export const THEME_TOKENS = {
  light: {
    bg: 'oklch(0.985 0.006 95)',
    surface: 'oklch(1 0 0)',
    surface2: 'oklch(0.975 0.006 95)',
    border: 'oklch(0.915 0.006 95)',
    text: 'oklch(0.30 0.02 90)',
    muted: 'oklch(0.56 0.016 90)',
    sidebar: 'oklch(0.972 0.006 95)',
    inputbg: 'oklch(0.978 0.006 95)',
    dotFill: 'transparent',
  },
  dark: {
    bg: 'oklch(0.205 0.014 260)',
    surface: 'oklch(0.245 0.016 260)',
    surface2: 'oklch(0.225 0.015 260)',
    border: 'oklch(0.32 0.018 260)',
    text: 'oklch(0.95 0.008 260)',
    muted: 'oklch(0.70 0.02 260)',
    sidebar: 'oklch(0.22 0.014 260)',
    inputbg: 'oklch(0.28 0.017 260)',
    dotFill: 'oklch(0.95 0.008 260)',
  },
};

/** localStorage key used to persist the chosen theme across sessions. */
export const STORAGE_KEY = 'ft_theme';

/**
 * Read the stored theme. Returns 'light' when unset, invalid, or when
 * localStorage throws (private browsing, disabled storage, etc.) — never throws.
 * @param {Storage} storage
 * @returns {'light'|'dark'}
 */
export function getStoredTheme(storage = typeof localStorage !== 'undefined' ? localStorage : undefined) {
  try {
    const raw = storage ? storage.getItem(STORAGE_KEY) : null;
    return raw === 'dark' ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

/**
 * Persist the theme. Swallows any storage error (private browsing, quota, etc.).
 * @param {'light'|'dark'} theme
 * @param {Storage} storage
 */
export function setStoredTheme(theme, storage = typeof localStorage !== 'undefined' ? localStorage : undefined) {
  try {
    if (storage) storage.setItem(STORAGE_KEY, theme);
  } catch {
    // Swallow — persistence is best-effort only.
  }
}

/**
 * Pure toggle: 'light' <-> 'dark'.
 * @param {'light'|'dark'} theme
 * @returns {'light'|'dark'}
 */
export function nextTheme(theme) {
  return theme === 'dark' ? 'light' : 'dark';
}

/**
 * Write the theme's CSS custom properties onto `root`, and update the
 * #theme-toggle button's label/dot if present.
 * @param {'light'|'dark'} theme
 * @param {HTMLElement} root  Usually document.documentElement.
 */
export function applyTheme(theme, root = document.documentElement) {
  const tokens = THEME_TOKENS[theme] ?? THEME_TOKENS.light;
  const doc = root.ownerDocument ?? document;

  root.style.setProperty('--bg', tokens.bg);
  root.style.setProperty('--surface', tokens.surface);
  root.style.setProperty('--surface2', tokens.surface2);
  root.style.setProperty('--border', tokens.border);
  root.style.setProperty('--text', tokens.text);
  root.style.setProperty('--muted', tokens.muted);
  root.style.setProperty('--sidebar', tokens.sidebar);
  root.style.setProperty('--inputbg', tokens.inputbg);
  root.style.setProperty('--accent', ACCENT);

  const label = doc.getElementById ? doc.getElementById('theme-label') : null;
  if (label) label.textContent = theme === 'dark' ? 'Dark' : 'Light';

  const dot = doc.getElementById ? doc.getElementById('theme-dot') : null;
  if (dot) dot.style.backgroundColor = tokens.dotFill;
}

/**
 * Wire up the theme toggle: read the stored theme, apply it immediately
 * (avoids a flash of the wrong theme), and bind #theme-toggle's click to
 * toggle -> persist -> apply -> optional onChange(newTheme) callback.
 *
 * @param {{ root?: Document, onChange?: (theme: 'light'|'dark') => void }} options
 * @returns {{ destroy(): void }}
 */
export function initTheme({ root = document, onChange } = {}) {
  let theme = getStoredTheme();
  applyTheme(theme, root.documentElement ?? root);

  const toggleBtn = root.getElementById ? root.getElementById('theme-toggle') : null;

  function handleClick() {
    theme = nextTheme(theme);
    setStoredTheme(theme);
    applyTheme(theme, root.documentElement ?? root);
    if (onChange) onChange(theme);
  }

  if (toggleBtn) {
    toggleBtn.addEventListener('click', handleClick);
  }

  function destroy() {
    if (toggleBtn) toggleBtn.removeEventListener('click', handleClick);
  }

  return { destroy };
}
