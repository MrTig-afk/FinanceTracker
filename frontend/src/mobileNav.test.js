/**
 * mobileNav.test.js — DOM tests for the mobile hamburger drawer toggle.
 * jsdom provides the DOM. No network, no data.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { createMobileNav } from './mobileNav.js';

const HTML = `
  <button id="nav-toggle" aria-expanded="false"></button>
  <nav id="sidebar-nav">
    <a href="#" class="nav-item" data-view="overview"><span class="nav-dot"></span>Overview</a>
    <a href="#" class="nav-item" data-view="trends"><span class="nav-dot"></span>Trends</a>
  </nav>
  <div id="nav-backdrop"></div>
`;

let ctrl;
const toggle = () => document.getElementById('nav-toggle');
const nav = () => document.getElementById('sidebar-nav');
const backdrop = () => document.getElementById('nav-backdrop');

beforeEach(() => {
  document.body.innerHTML = HTML;
  ctrl = createMobileNav({ root: document });
});

afterEach(() => {
  if (ctrl) ctrl.destroy();
  ctrl = null;
  document.body.innerHTML = '';
});

describe('createMobileNav', () => {
  it('starts closed', () => {
    expect(ctrl.isOpen).toBe(false);
    expect(nav().classList.contains('is-open')).toBe(false);
    expect(toggle().getAttribute('aria-expanded')).toBe('false');
  });

  it('opens on the hamburger click (nav + backdrop + aria)', () => {
    toggle().dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(ctrl.isOpen).toBe(true);
    expect(nav().classList.contains('is-open')).toBe(true);
    expect(backdrop().classList.contains('is-open')).toBe(true);
    expect(toggle().getAttribute('aria-expanded')).toBe('true');
  });

  it('toggles closed on a second click', () => {
    toggle().dispatchEvent(new MouseEvent('click', { bubbles: true }));
    toggle().dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(ctrl.isOpen).toBe(false);
    expect(nav().classList.contains('is-open')).toBe(false);
    expect(toggle().getAttribute('aria-expanded')).toBe('false');
  });

  it('closes when the backdrop is clicked', () => {
    ctrl.open();
    backdrop().dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(ctrl.isOpen).toBe(false);
  });

  it('closes on Escape', () => {
    ctrl.open();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(ctrl.isOpen).toBe(false);
  });

  it('closes when a nav item is chosen', () => {
    ctrl.open();
    document.querySelector('a.nav-item').dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(ctrl.isOpen).toBe(false);
  });

  it('destroy() unbinds so later clicks do nothing', () => {
    ctrl.destroy();
    toggle().dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(nav().classList.contains('is-open')).toBe(false);
  });
});
