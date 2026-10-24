#!/usr/bin/env node
// Theme contract check (docs/plans/ui-themes.md sections 3.1, 3.3, 3.4, 3.6).
// Plain Node 20 ES module, no dependencies. Run from the repo root:
//   node frontend/scripts/check-theme.mjs
// Exits non-zero if any contrast pair, name-parity rule, inline-script rule or inline-script
// behavior (OS-preference default, saved choice persists) fails.

import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, relative } from 'node:path';
import vm from 'node:vm';

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), '..');
const REPO = join(FRONTEND, '..');
const THEME_CSS = join(FRONTEND, 'theme.css');
const PRESET_TS = join(FRONTEND, 'tailwind.preset.ts');
const APPS = ['host', 'player', 'admin'];

const failures = [];
const fail = (msg) => failures.push(msg);
const rel = (p) => relative(REPO, p);

// ---------------------------------------------------------------- theme.css parsing

function stripBlockComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));
}

// Non-color tokens. Themed ones must exist in every theme block; static ones (motion, blur)
// live in one plain :root block. None of them is contrast-checked as a color.
const THEMED_NON_COLOR = /^(shadow|alpha)-/;
const STATIC_NON_COLOR = /^(dur|ease|blur|stagger)-/;
/** Numeric --alpha-* values per theme, for the glass composite checks. */
const alphas = {};
/** Themed non-color token names per theme, for parity. */
const themedNonColor = {};

/** Returns { dark: Map<token, [r,g,b]>, light?: Map } from the :root theme blocks. */
function parseThemeCss(src) {
  const themes = {};
  const css = stripBlockComments(src);
  // Innermost rule blocks only; @media wrappers are skipped naturally.
  for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selector = m[1].trim();
    if (!selector.includes(':root')) continue;
    const parts = selector.split(',').map((s) => s.trim());
    const names = new Set();
    for (const p of parts) {
      const dt = p.match(/^:root\[data-theme=["']?(\w+)["']?\]$/);
      if (dt) names.add(dt[1]);
      else if (p === ':root') names.add('dark'); // plain :root is the dark baseline
    }
    if (names.size === 0) continue;
    const decls = [...m[2].matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)];
    if (decls.length === 0) continue;
    for (const name of names) {
      const map = (themes[name] ??= new Map());
      for (const [, token, raw] of decls) {
        if (STATIC_NON_COLOR.test(token)) continue;
        if (THEMED_NON_COLOR.test(token)) {
          (themedNonColor[name] ??= new Set()).add(token);
          if (token.startsWith('alpha-')) {
            const a = Number(raw.trim());
            if (!(a >= 0 && a <= 1)) fail(`theme.css [${name}] --${token}: "${raw.trim()}" is not a 0-1 number`);
            else (alphas[name] ??= new Map()).set(token, a);
          }
          continue;
        }
        const ch = raw.trim().split(/\s+/).map(Number);
        if (ch.length !== 3 || ch.some((n) => !Number.isInteger(n) || n < 0 || n > 255)) {
          fail(`theme.css [${name}] --${token}: value "${raw.trim()}" is not three 0-255 RGB channels`);
          continue;
        }
        map.set(token, ch);
      }
    }
  }
  return themes;
}

// ---------------------------------------------------------------- WCAG 2.1 math

const lin = (c) => {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
};
const luminance = ([r, g, b]) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
function contrast(a, b) {
  const [l1, l2] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (l1 + 0.05) / (l2 + 0.05);
}
// Composite fg at alpha over bg (sRGB channel blend, as browsers do).
const blend = (fg, bg, alpha) => fg.map((c, i) => Math.round(c * alpha + bg[i] * (1 - alpha)));
const toHex = (ch) => '#' + ch.map((c) => c.toString(16).padStart(2, '0')).join('');

// ---------------------------------------------------------------- pair matrix (3.3)

function buildPairs() {
  const pairs = []; // { fg, bg, min, group, composite? }
  const add = (group, fg, bg, min, composite) => pairs.push({ group, fg, bg, min, composite });
  const BASES = ['page', 'surface', 'raised'];
  const STATUS = ['success', 'warning', 'danger'];

  for (const fg of ['primary', 'secondary', 'muted', 'accent-text', ...STATUS])
    for (const bg of BASES) add('text', fg, bg, 4.5);
  for (const fg of ['primary', 'muted', 'accent-text']) add('text', fg, 'accent-hint', 4.5); // accent-text: Badge

  for (const s of STATUS.filter((x) => x !== 'success')) add('fg-on-fill', `${s}-fg`, s, 4.5); // success-fg is not a token
  add('fg-on-fill', 'accent-fg', 'accent', 4.5);
  add('fg-on-fill', 'accent-fg', 'accent-hover', 4.5);
  add('fg-on-fill', 'danger-fg', 'danger-hover', 4.5);

  for (const bg of BASES) add('ui', 'ring', bg, 3);
  for (const bg of [...BASES, 'accent-hint']) add('ui', 'line-strong', bg, 3);
  for (const n of [1, 2, 4]) add('ui', `timer-${n}`, 'timer-track', 3);
  add('ui', 'accent-text', 'accent-hint', 3);
  add('ui', 'accent', 'page', 3);
  add('ui', 'accent', 'surface', 3);
  for (const fill of ['accent', 'success', 'warning', 'danger']) add('ui', fill, 'track', 3); // chart bar / Progress fill on its empty track

  for (const s of STATUS) add('tint', s, `${s}-tint`, 4.5);
  for (const s of STATUS) add('tint', 'primary', `${s}-tint`, 4.5);

  // Composite: text on surface at 60% over page.
  for (const fg of ['primary', 'secondary', 'muted', 'accent-text', 'danger'])
    add('composite', fg, 'surface/60 over page', 4.5, { bg: 'surface', alpha: 0.6, under: 'page' });
  return pairs;
}

function checkContrast(themeName, tokens) {
  const rows = [];
  for (const p of buildPairs()) {
    const fg = tokens.get(p.fg);
    let bg;
    if (p.composite) {
      const top = tokens.get(p.composite.bg);
      const under = tokens.get(p.composite.under);
      bg = top && under ? blend(top, under, p.composite.alpha) : undefined;
    } else bg = tokens.get(p.bg);
    if (!fg || !bg) {
      rows.push({ ...p, ratio: NaN, ok: false, note: 'missing token' });
      fail(`[${themeName}] ${p.fg} on ${p.bg}: missing token`);
      continue;
    }
    const ratio = contrast(fg, bg);
    const ok = ratio >= p.min; // unrounded, as WCAG requires
    if (!ok) fail(`[${themeName}] ${p.fg} on ${p.bg}: ${ratio.toFixed(2)} < ${p.min}`);
    rows.push({ ...p, ratio, ok, fgHex: toHex(fg), bgHex: toHex(bg) });
  }
  return rows;
}

function printTable(themeName, rows) {
  console.log(`\nTheme "${themeName}": ${rows.length} pairs`);
  const head = ['group', 'foreground', 'background', 'fg hex', 'bg hex', 'ratio', 'min', 'result'];
  const body = rows.map((r) => [
    r.group, r.fg, r.bg, r.fgHex ?? '-', r.bgHex ?? '-',
    Number.isNaN(r.ratio) ? '-' : r.ratio.toFixed(2), String(r.min), r.ok ? 'PASS' : 'FAIL',
  ]);
  const widths = head.map((h, i) => Math.max(h.length, ...body.map((b) => b[i].length)));
  const line = (cells) => cells.map((c, i) => c.padEnd(widths[i])).join('  ');
  console.log(line(head));
  console.log(widths.map((w) => '-'.repeat(w)).join('  '));
  for (const b of body) console.log(line(b));
}


// ---------------------------------------------------------------- glass over the page backdrop (section 12)

// The backdrop (theme.css body::before) is a page -> page-2 gradient with three accent blobs at
// --alpha-blob (blob-3 at 0.8x). Worst case is checked: every gradient endpoint, each blob at its
// peak over each endpoint, and each pair of blobs overlapping. Blur only averages these colors,
// so it cannot produce a color outside this set.
const GLASS_TEXT = ['primary', 'secondary', 'muted', 'accent-text', 'success', 'warning', 'danger'];
const GLASS_LEVELS = [
  ['backdrop (no glass)', null],
  ['glass-subtle', 'alpha-glass-subtle'],
  ['glass', 'alpha-glass'],
  ['glass-strong', 'alpha-glass-strong'],
];

function backdropColors(tokens, a) {
  const out = [];
  const blobA = a.get('alpha-blob');
  const blobs = [1, 2, 3].map((n) => ({ name: `blob-${n}`, ch: tokens.get(`blob-${n}`), alpha: n === 3 ? blobA * 0.8 : blobA }));
  for (const baseName of ['page', 'page-2']) {
    const base = tokens.get(baseName);
    if (!base) continue;
    out.push({ label: baseName, ch: base });
    for (const b of blobs) {
      if (!b.ch) continue;
      const one = blend(b.ch, base, b.alpha);
      out.push({ label: `${b.name} over ${baseName}`, ch: one });
      for (const c of blobs) {
        if (c === b || !c.ch) continue;
        out.push({ label: `${c.name}+${b.name} over ${baseName}`, ch: blend(c.ch, one, c.alpha) });
      }
    }
  }
  return out;
}

function checkGlass(themeName, tokens) {
  const a = alphas[themeName] ?? new Map();
  const glass = tokens.get('glass');
  const missing = ['alpha-blob', 'alpha-glass', 'alpha-glass-strong', 'alpha-glass-subtle'].filter((k) => !a.has(k));
  if (!glass || missing.length) {
    fail(`[${themeName}] glass check: missing ${!glass ? '--glass ' : ''}${missing.map((m) => `--${m}`).join(' ')}`);
    return;
  }
  const backs = backdropColors(tokens, a);
  console.log(`\nTheme "${themeName}": text on glass, worst case over ${backs.length} backdrop colors (min 4.5)`);
  const head = ['surface', 'foreground', 'worst background', 'bg hex', 'ratio', 'result'];
  const body = [];
  for (const [label, alphaKey] of GLASS_LEVELS) {
    const surfaces = backs.map((b) => ({ label: b.label, ch: alphaKey ? blend(glass, b.ch, a.get(alphaKey)) : b.ch }));
    for (const fgName of GLASS_TEXT) {
      const fg = tokens.get(fgName);
      if (!fg) continue;
      let worst = null;
      for (const s of surfaces) {
        const r = contrast(fg, s.ch);
        if (!worst || r < worst.r) worst = { r, s };
      }
      const ok = worst.r >= 4.5;
      if (!ok) fail(`[${themeName}] ${fgName} on ${label} over ${worst.s.label}: ${worst.r.toFixed(2)} < 4.5`);
      body.push([label, fgName, worst.s.label, toHex(worst.s.ch), worst.r.toFixed(2), ok ? 'PASS' : 'FAIL']);
    }
  }
  const widths = head.map((h, i) => Math.max(h.length, ...body.map((b) => b[i].length)));
  const line = (cells) => cells.map((c, i) => c.padEnd(widths[i])).join('  ');
  console.log(line(head));
  console.log(widths.map((w) => '-'.repeat(w)).join('  '));
  for (const b of body) console.log(line(b));
}

// ---------------------------------------------------------------- preset name parity (3.1)

// Contract table from section 3.1: which Tailwind key(s) each token lives under.
// success has no -fg token (nothing uses it); warning and danger do.
const STATUS_ALL = ['success', 'warning', 'danger'].flatMap((s) => (s === 'success' ? [s, `${s}-tint`] : [s, `${s}-fg`, `${s}-tint`]));
const CONTRACT = {
  colors: [
    'accent', 'accent-hover', 'accent-fg', 'accent-hint', 'accent-text', ...STATUS_ALL,
    'danger-hover', 'ring', 'timer-1', 'timer-2', 'timer-4', 'timer-track',
  ],
  backgroundColor: ['page', 'page-2', 'surface', 'raised', 'track', 'glass', 'blob-1', 'blob-2', 'blob-3'],
  textColor: ['primary', 'secondary', 'muted'],
  borderColor: ['line', 'line-strong', 'glass-edge', 'glass-highlight'],
  divideColor: ['line', 'line-strong'],
  ringOffsetColor: ['page'],
  placeholderColor: ['muted'],
};

// Only the CONTRACT keys hold color tokens. Every other preset key (fontSize, borderRadius,
// boxShadow, animation, keyframe steps, ...) is skipped, but it must not map a color variable.

/** Returns Map<tailwindKey, Map<className, varName>> without executing TypeScript. */
function parsePreset(src) {
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
  const out = new Map();
  for (const m of code.matchAll(/['"]?(\w+)['"]?\s*:\s*\{([^{}]*)\}/g)) {
    const entries = [...m[2].matchAll(/['"]?([\w-]+)['"]?\s*:\s*['"`]([^'"`]*)['"`]/g)];
    if (entries.length === 0) continue;
    const map = new Map();
    for (const [, cls, value] of entries) {
      const v = value.match(/var\(--([\w-]+)\)/);
      map.set(cls, v ? v[1] : null);
    }
    out.set(m[1], map);
  }
  return out;
}

function checkPreset(themeTokens) {
  const preset = parsePreset(readFileSync(PRESET_TS, 'utf8'));
  const cssNames = new Set(themeTokens);
  const presetNames = new Set();
  let problems = 0;
  const p = (msg) => { problems++; fail(`preset: ${msg}`); };

  for (const [key, map] of preset) {
    if (!CONTRACT[key]) {
      for (const [cls, varName] of map)
        if (varName && !THEMED_NON_COLOR.test(varName) && !STATIC_NON_COLOR.test(varName))
          p(`${key}.${cls} maps color --${varName} under a key outside the 3.1 table`);
      continue;
    }
    for (const [cls, varName] of map) {
      presetNames.add(cls);
      if (varName === null) p(`${key}.${cls} does not reference a CSS variable`);
      else if (varName !== cls) p(`${key}.${cls} references --${varName} (expected --${cls})`);
      if (!CONTRACT[key].includes(cls)) p(`${key}.${cls}: token not allowed under "${key}" by the 3.1 table`);
    }
  }
  for (const [key, list] of Object.entries(CONTRACT))
    for (const cls of list)
      if (!preset.get(key)?.has(cls)) p(`${cls} missing under "${key}" (required by the 3.1 table)`);

  const onlyCss = [...cssNames].filter((n) => !presetNames.has(n));
  const onlyPreset = [...presetNames].filter((n) => !cssNames.has(n));
  if (onlyCss.length) p(`in theme.css but not in preset: ${onlyCss.join(', ')}`);
  if (onlyPreset.length) p(`in preset but not in theme.css: ${onlyPreset.join(', ')}`);

  console.log(
    `\nName parity: ${cssNames.size} tokens in theme.css, ${presetNames.size} distinct names in preset ` +
      `across ${preset.size} Tailwind keys: ${problems ? `FAIL (${problems} problems)` : 'PASS'}`,
  );
}

// ---------------------------------------------------------------- inline theme scripts (3.4)

function checkInlineScripts(themes) {
  const found = [];
  for (const app of APPS) {
    const file = join(FRONTEND, app, 'index.html');
    if (!existsSync(file)) continue;
    const html = readFileSync(file, 'utf8');
    const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)]
      .map((m) => m[1])
      .filter((s) => /dataset\.theme|data-theme|buzzerTheme|theme-color/i.test(s));
    if (blocks.length) found.push({ app, file, script: blocks.join('\n').trim() });
  }
  if (found.length === 0) {
    console.log('\nInline theme scripts: none in frontend/*/index.html yet (Phase 2): SKIP');
    return;
  }
  let problems = 0;
  if (found.length !== APPS.length) {
    problems++;
    const missing = APPS.filter((a) => !found.some((f) => f.app === a));
    fail(`inline theme script present in ${found.map((f) => f.app).join(', ')} but missing in ${missing.join(', ')}`);
  }
  const norm = (s) => s.replace(/\r\n/g, '\n').split('\n').map((l) => l.trim()).join('\n');
  for (const f of found.slice(1)) {
    if (norm(f.script) !== norm(found[0].script)) {
      problems++;
      fail(`inline theme script in ${rel(f.file)} differs from ${rel(found[0].file)}`);
    }
  }
  // theme-color values: each hex must sit on a line naming its theme (light/dark).
  for (const f of found) {
    for (const line of f.script.split('\n')) {
      for (const hm of line.matchAll(/#[0-9a-fA-F]{6}\b/g)) {
        const before = line.slice(0, hm.index).match(/\b(light|dark)\b(?![\s\S]*\b(light|dark)\b)/);
        const theme = before?.[1];
        if (!theme) {
          problems++;
          fail(`${rel(f.file)}: theme-color ${hm[0]} is not labelled light/dark on its line`);
          continue;
        }
        const page = themes[theme]?.get('page');
        if (!page) {
          problems++;
          fail(`${rel(f.file)}: theme-color for "${theme}" but theme.css has no ${theme} page token`);
        } else if (hm[0].toLowerCase() !== toHex(page)) {
          problems++;
          fail(`${rel(f.file)}: ${theme} theme-color ${hm[0]} != page ${toHex(page)}`);
        }
      }
    }
  }
  console.log(`\nInline theme scripts: ${found.length} found: ${problems ? `FAIL (${problems} problems)` : 'PASS'}`);
  checkScriptBehavior(found);
}

/**
 * Runs each app's inline script in a fake browser and checks the two 3.4 behaviors:
 * with nothing saved, the first theme follows prefers-color-scheme; a choice made with
 * window.buzzerTheme.set() is saved under `buzzer-theme` and wins on the next page load.
 */
function checkScriptBehavior(found) {
  function load(script, storage, osLight) {
    const root = { dataset: {}, style: {} };
    const sandbox = {
      document: { documentElement: root, querySelector: () => null },
      localStorage: {
        getItem: (k) => (storage.has(k) ? storage.get(k) : null),
        setItem: (k, v) => storage.set(k, String(v)),
      },
      matchMedia: (q) => ({ matches: q === '(prefers-color-scheme: light)' && osLight, addEventListener() {} }),
      addEventListener() {},
    };
    sandbox.window = sandbox;
    vm.runInNewContext(script, sandbox);
    return { theme: () => root.dataset.theme, set: (t) => sandbox.buzzerTheme.set(t) };
  }
  const os = [];
  const saved = [];
  for (const f of found) {
    const expect = (cond, msg) => {
      if (!cond) fail(`${rel(f.file)}: ${msg}`);
      return cond;
    };
    let osOk = true;
    let saveOk = true;
    try {
      osOk = expect(load(f.script, new Map(), true).theme() === 'light', 'nothing saved + OS light should start light') && osOk;
      osOk = expect(load(f.script, new Map(), false).theme() === 'dark', 'nothing saved + OS dark should start dark') && osOk;
      const storage = new Map();
      load(f.script, storage, false).set('light');
      saveOk = expect(storage.get('buzzer-theme') === 'light', 'buzzerTheme.set("light") should save buzzer-theme') && saveOk;
      saveOk = expect(load(f.script, storage, false).theme() === 'light', 'saved light should win over OS dark on reload') && saveOk;
    } catch (e) {
      osOk = saveOk = expect(false, `inline theme script threw in the fake browser: ${e.message}`);
    }
    if (osOk) os.push(f.app);
    if (saveOk) saved.push(f.app);
  }
  const line = (ok) => (ok.length === found.length ? `PASS (${ok.join(', ')})` : `FAIL (passes only in ${ok.join(', ') || 'none'})`);
  console.log(`OS-preference default: ${line(os)}`);
  console.log(`Persistence across reloads: ${line(saved)}`);
}

// ---------------------------------------------------------------- ThemeToggle copies (3.4)

/** The three apps keep identical ThemeToggle.tsx copies (there is no shared package). */
function checkToggleCopies() {
  const copies = APPS.map((app) => ({ app, file: join(FRONTEND, app, 'src/components/ui/ThemeToggle.tsx') })).filter((c) =>
    existsSync(c.file),
  );
  if (copies.length < 2) {
    console.log('\nThemeToggle copies: fewer than two found: SKIP');
    return;
  }
  const base = readFileSync(copies[0].file, 'utf8');
  let problems = 0;
  for (const c of copies.slice(1)) {
    if (readFileSync(c.file, 'utf8') !== base) {
      problems++;
      fail(`${rel(c.file)} differs from ${rel(copies[0].file)} (ThemeToggle copies must be identical)`);
    }
  }
  console.log(`\nThemeToggle copies: ${copies.length} found: ${problems ? `FAIL (${problems} differ)` : 'PASS (identical)'}`);
}

// ---------------------------------------------------------------- PinGrid copies

/** host and player keep copies of PinGrid.tsx; only the header comment line naming the other app may differ. */
function checkPinGridCopies() {
  const copies = ['host', 'player'].map((app) => join(FRONTEND, app, 'src/components/PinGrid.tsx')).filter(existsSync);
  if (copies.length < 2) {
    console.log('\nPinGrid copies: fewer than two found: SKIP');
    return;
  }
  // Allowed difference: the first comment line ("The <other> app has a copy of this file").
  const norm = (f) => readFileSync(f, 'utf8').replace(/^\/\/ Pin Drop grid \(T7\)\. The \w+ app has a copy of this file;.*$/m, '// HEADER');
  const same = norm(copies[0]) === norm(copies[1]);
  if (!same) fail(`${rel(copies[1])} differs from ${rel(copies[0])} (PinGrid copies must match except the header comment)`);
  console.log(`\nPinGrid copies: ${same ? 'PASS (identical except header comment)' : 'FAIL'}`);
}

// ---------------------------------------------------------------- main

const themes = parseThemeCss(readFileSync(THEME_CSS, 'utf8'));
const themeNames = Object.keys(themes);
if (!themes.dark) fail('theme.css: no dark block (":root, :root[data-theme=\\"dark\\"]") found');
console.log(`theme.css: themes found: ${themeNames.join(', ') || 'none'}`);

const baseline = themes.dark ?? themes[themeNames[0]] ?? new Map();
for (const name of themeNames) {
  if (name === 'dark') continue;
  const missing = [...baseline.keys()].filter((t) => !themes[name].has(t));
  const extra = [...themes[name].keys()].filter((t) => !baseline.has(t));
  if (missing.length) fail(`theme.css [${name}] missing tokens: ${missing.join(', ')}`);
  if (extra.length) fail(`theme.css [${name}] has tokens not in dark: ${extra.join(', ')}`);
}

const baseNon = themedNonColor.dark ?? new Set();
for (const name of themeNames) {
  if (name === 'dark') continue;
  const other = themedNonColor[name] ?? new Set();
  const missing = [...baseNon].filter((t) => !other.has(t));
  const extra = [...other].filter((t) => !baseNon.has(t));
  if (missing.length) fail(`theme.css [${name}] missing themed non-color tokens: ${missing.join(', ')}`);
  if (extra.length) fail(`theme.css [${name}] has themed non-color tokens not in dark: ${extra.join(', ')}`);
}

for (const name of themeNames) {
  printTable(name, checkContrast(name, themes[name]));
  checkGlass(name, themes[name]);
}
checkPreset(baseline.keys());
checkInlineScripts(themes);
checkToggleCopies();
checkPinGridCopies();

if (failures.length) {
  console.log(`\nFAILED (${failures.length}):`);
  for (const f of failures) console.log(`  - ${f}`);
  process.exit(1);
}
console.log('\nAll theme checks passed.');
