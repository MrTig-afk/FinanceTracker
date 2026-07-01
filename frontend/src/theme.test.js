/**
 * theme.test.js — unit tests for theme.js (tokens + DOM binding).
 * All fixtures are synthetic; jsdom provides document/localStorage.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  THEME_TOKENS,
  STORAGE_KEY,
  getStoredTheme,
  setStoredTheme,
  nextTheme,
  applyTheme,
  initTheme,
} from './theme.js';

const REQUIRED_KEYS = [
  'bg', 'surface', 'surface2', 'border', 'text', 'muted',
  'sidebar', 'inputbg', 'dotFill',
];

// ---------------------------------------------------------------------------
// THEME_TOKENS
// ---------------------------------------------------------------------------

describe('THEME_TOKENS', () => {
  it('light contains all required keys as non-empty strings', () => {
    REQUIRED_KEYS.forEach((key) => {
      expect(THEME_TOKENS.light).toHaveProperty(key);
      expect(typeof THEME_TOKENS.light[key]).toBe('string');
      expect(THEME_TOKENS.light[key].length).toBeGreaterThan(0);
    });
  });

  it('dark contains all required keys as non-empty strings', () => {
    REQUIRED_KEYS.forEach((key) => {
      expect(THEME_TOKENS.dark).toHaveProperty(key);
      expect(typeof THEME_TOKENS.dark[key]).toBe('string');
      expect(THEME_TOKENS.dark[key].length).toBeGreaterThan(0);
    });
  });
});

// ---------------------------------------------------------------------------
// nextTheme
// ---------------------------------------------------------------------------

describe('nextTheme', () => {
  it('toggles light -> dark', () => {
    expect(nextTheme('light')).toBe('dark');
  });

  it('toggles dark -> light', () => {
    expect(nextTheme('dark')).toBe('light');
  });
});

// ---------------------------------------------------------------------------
// getStoredTheme / setStoredTheme
// ---------------------------------------------------------------------------

function fakeStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, v),
  };
}

describe('getStoredTheme', () => {
  it("returns 'light' when storage is empty", () => {
    expect(getStoredTheme(fakeStorage())).toBe('light');
  });

  it("returns 'light' when storage holds garbage", () => {
    expect(getStoredTheme(fakeStorage({ [STORAGE_KEY]: 'not-a-theme' }))).toBe('light');
  });

  it("returns the stored value when it is 'dark'", () => {
    expect(getStoredTheme(fakeStorage({ [STORAGE_KEY]: 'dark' }))).toBe('dark');
  });

  it('never throws when storage.getItem throws', () => {
    const throwing = {
      getItem: () => {
        throw new Error('blocked');
      },
    };
    expect(() => getStoredTheme(throwing)).not.toThrow();
    expect(getStoredTheme(throwing)).toBe('light');
  });
});

describe('setStoredTheme', () => {
  it('round-trips through getStoredTheme', () => {
    const storage = fakeStorage();
    setStoredTheme('dark', storage);
    expect(getStoredTheme(storage)).toBe('dark');
  });

  it('swallows storage errors', () => {
    const throwing = {
      setItem: () => {
        throw new Error('blocked');
      },
    };
    expect(() => setStoredTheme('dark', throwing)).not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// applyTheme
// ---------------------------------------------------------------------------

describe('applyTheme', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <span id="theme-label"></span>
      <span id="theme-dot"></span>
    `;
    document.documentElement.removeAttribute('style');
  });

  it('sets --bg to the dark token value', () => {
    applyTheme('dark', document.documentElement);
    expect(document.documentElement.style.getPropertyValue('--bg')).toBe(THEME_TOKENS.dark.bg);
  });

  it('sets --surface to the light token value', () => {
    applyTheme('light', document.documentElement);
    expect(document.documentElement.style.getPropertyValue('--surface')).toBe(
      THEME_TOKENS.light.surface,
    );
  });

  it('updates #theme-label to "Dark" for the dark theme', () => {
    applyTheme('dark', document.documentElement);
    expect(document.getElementById('theme-label').textContent).toBe('Dark');
  });

  it('updates #theme-label to "Light" for the light theme', () => {
    applyTheme('light', document.documentElement);
    expect(document.getElementById('theme-label').textContent).toBe('Light');
  });

  it('updates #theme-dot background to the token dotFill', () => {
    applyTheme('dark', document.documentElement);
    expect(document.getElementById('theme-dot').style.backgroundColor).toBeTruthy();
  });

  it('does not throw when #theme-label/#theme-dot are absent', () => {
    document.body.innerHTML = '';
    expect(() => applyTheme('dark', document.documentElement)).not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// initTheme
// ---------------------------------------------------------------------------

describe('initTheme', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <button id="theme-toggle"><span id="theme-dot"></span><span id="theme-label"></span></button>
    `;
    document.documentElement.removeAttribute('style');
    localStorage.clear();
  });

  it('applies the stored theme at init', () => {
    localStorage.setItem(STORAGE_KEY, 'dark');
    initTheme({ root: document });
    expect(document.documentElement.style.getPropertyValue('--bg')).toBe(THEME_TOKENS.dark.bg);
  });

  it('clicking #theme-toggle flips the stored theme and calls onChange', () => {
    const onChange = vi.fn();
    initTheme({ root: document, onChange });

    document.getElementById('theme-toggle').click();

    expect(onChange).toHaveBeenCalledWith('dark');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('dark');
    expect(document.documentElement.style.getPropertyValue('--bg')).toBe(THEME_TOKENS.dark.bg);
  });

  it('a second click toggles back to light', () => {
    const onChange = vi.fn();
    initTheme({ root: document, onChange });

    document.getElementById('theme-toggle').click();
    document.getElementById('theme-toggle').click();

    expect(onChange).toHaveBeenLastCalledWith('light');
  });

  it('destroy() removes the click listener', () => {
    const onChange = vi.fn();
    const { destroy } = initTheme({ root: document, onChange });
    destroy();

    document.getElementById('theme-toggle').click();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('does not throw when #theme-toggle is absent', () => {
    document.body.innerHTML = '';
    expect(() => initTheme({ root: document })).not.toThrow();
  });
});
