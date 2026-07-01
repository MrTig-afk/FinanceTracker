/**
 * categoryContext.test.js — unit tests for buildContextPrompt (categoryContext.js).
 * ALL fixtures are SYNTHETIC hint strings, never the D2 defaults and never real
 * transaction data. No network. The golden string here is byte-identical to the
 * one in backend/analyser/test_analyser.py (TestBuildContextPromptGolden) —
 * cross-language pin so the two builders cannot drift.
 */

import { describe, it, expect } from 'vitest';
import { buildContextPrompt } from './categoryContext.js';

// ---------------------------------------------------------------------------
// SYNTHETIC categories — invented hint strings, NOT the D2 defaults and NOT
// any transaction text. color/position are irrelevant to buildContextPrompt.
// ---------------------------------------------------------------------------

const GOLDEN_CATEGORIES = [
  { name: 'Alpha', color: '#111111', hints: 'SYNTH GROCER A, SYNTH GROCER B', position: 0 },
  { name: 'Beta', color: '#222222', hints: '   ', position: 1 },
  { name: 'Gamma', color: '#333333', hints: 'SYNTH  MULTI   SPACE\n\nHINT', position: 2 },
];

// Byte-identical to the Python golden fixture in backend/analyser/test_analyser.py.
const GOLDEN_PROMPT =
  'TAXONOMY & CONTEXT\n' +
  '------------------\n' +
  '- Alpha\n' +
  '    SYNTH GROCER A, SYNTH GROCER B\n' +
  '\n' +
  '- Beta\n' +
  '    (no extra context)\n' +
  '\n' +
  '- Gamma\n' +
  '    SYNTH MULTI SPACE HINT';

describe('buildContextPrompt', () => {
  it('matches the shared golden string exactly', () => {
    expect(buildContextPrompt(GOLDEN_CATEGORIES)).toBe(GOLDEN_PROMPT);
  });

  it('char count equals the string length', () => {
    expect(buildContextPrompt(GOLDEN_CATEGORIES).length).toBe(GOLDEN_PROMPT.length);
  });

  it('empty hints become "(no extra context)"', () => {
    expect(buildContextPrompt(GOLDEN_CATEGORIES)).toContain('(no extra context)');
  });

  it('collapses multi-space and newline hints, trimmed', () => {
    const result = buildContextPrompt(GOLDEN_CATEGORIES);
    expect(result).toContain('SYNTH MULTI SPACE HINT');
    expect(result).not.toContain('SYNTH  MULTI'); // original double space gone
    expect(result).not.toContain('\n\nHINT'); // original blank-line break gone
  });

  it('starts with header, separator, then the first entry', () => {
    expect(buildContextPrompt(GOLDEN_CATEGORIES).startsWith(
      'TAXONOMY & CONTEXT\n------------------\n- Alpha',
    )).toBe(true);
  });

  it('joins categories with a blank line', () => {
    const result = buildContextPrompt(GOLDEN_CATEGORIES);
    expect(result).toContain('\n\n- Beta');
    expect(result).toContain('\n\n- Gamma');
  });

  it('returns the header-only form for an empty list', () => {
    expect(buildContextPrompt([])).toBe('TAXONOMY & CONTEXT\n------------------\n');
  });

  it('falls back to "Untitled" for an empty name', () => {
    const result = buildContextPrompt([{ name: '', hints: 'SYNTH HINT' }]);
    expect(result).toContain('- Untitled');
  });
});
