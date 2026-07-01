/**
 * categoryContext.js — JS mirror of the canonical "TAXONOMY & CONTEXT" preamble
 * builder (backend/analyser/context.py::build_context_prompt). Byte-identical
 * logic, copied VERBATIM from the mockup buildPrompt()
 * (`Category Context.dc.html` lines 147-157). Both are pinned to the same
 * golden fixture in their respective test suites (categoryContext.test.js /
 * backend/analyser/test_analyser.py) so they cannot drift.
 *
 * Retained as the format-parity reference for the backend builder even though
 * the UI no longer renders a generated-prompt preview — not used in the UI.
 *
 * Pure string builder — no IO, no network, no DOM.
 */

/**
 * Build the "TAXONOMY & CONTEXT" preamble string from an array of categories.
 * @param {Array<{name: string, hints: string}>} cats
 * @returns {string}
 */
export function buildContextPrompt(cats) {
  const head = ['TAXONOMY & CONTEXT', '------------------'];
  const body = cats.map((c) => {
    const h = (c.hints || '').replace(/\s+/g, ' ').trim();
    return '- ' + (c.name || 'Untitled') + '\n    ' + (h ? h : '(no extra context)');
  });
  return head.join('\n') + '\n' + body.join('\n\n');
}
