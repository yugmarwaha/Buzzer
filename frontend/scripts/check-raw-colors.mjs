#!/usr/bin/env node
// Raw-color scanner (docs/plans/ui-themes.md sections 3.2, 3.5, 3.6).
// Plain Node 20 ES module, no dependencies. Run from the repo root:
//   node frontend/scripts/check-raw-colors.mjs            report mode, always exits 0
//   node frontend/scripts/check-raw-colors.mjs --strict   exits 1 on any finding outside the allowlist
//   add --verbose to print every finding with its line number
// Allowlist: frontend/scripts/raw-colors-allow.txt (repo-relative paths, # comments).

import { readFileSync, readdirSync, existsSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, relative, sep } from 'node:path';

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), '..');
const REPO = join(FRONTEND, '..');
const APPS = ['host', 'player', 'admin'];
const ALLOW_FILE = join(FRONTEND, 'scripts', 'raw-colors-allow.txt');
const THEME_CSS = join(FRONTEND, 'theme.css');

const args = new Set(process.argv.slice(2));
const STRICT = args.has('--strict');
const VERBOSE = args.has('--verbose');

// ---------------------------------------------------------------- rules

const PALETTE =
  'slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose';
const PREFIX =
  'bg|text|border(?:-[xytrblse])?|ring(?:-offset)?|from|to|via|divide|placeholder|outline|fill|stroke|decoration|shadow|accent|caret';
// Optional variant chain (hover:, md:, group-hover:, [&>svg]:) and important marker.
const VARIANTS = String.raw`(?:(?:[\w-]+|\[[^\]\s]+\]):)*!?`;
const OPACITY = String.raw`(?:\/(?:\d+|\[[^\]\s]+\]))?`;
const START = String.raw`(?<![\w-])`;
const END = String.raw`(?![\w-])`;
const NAMED =
  'white|black|red|green|blue|yellow|orange|purple|pink|gray|grey|silver|gold|cyan|magenta|navy|teal|maroon|lime|olive|aqua|fuchsia|brown|crimson|indigo|violet';
const TEXT_SIZES = 'xs|sm|base|lg|xl|\\d+xl'; // text-lg/7 is a line-height modifier, not opacity

// Order matters: earlier rules claim a span, later overlapping matches are dropped,
// so each class or literal is counted once.
const RULES = [
  {
    id: 'palette',
    label: 'raw Tailwind palette utility',
    re: new RegExp(`${START}${VARIANTS}(?:${PREFIX})-(?:(?:${PALETTE})-(?:50|[1-9]00|950)|white|black)${OPACITY}${END}`, 'g'),
  },
  {
    id: 'canvas-style',
    label: 'fillStyle/strokeStyle string literal',
    re: /\b(?:fillStyle|strokeStyle)\s*=\s*(['"`])[^'"`]*\1/g,
  },
  {
    // style={{ color: 'white' }}, `color: red;` in CSS strings, fill="red", stroke={'black'}
    id: 'named-color',
    label: 'named color in style/color/fill/stroke literal',
    re: new RegExp(
      String.raw`(?<![\w-])(?:color|background(?:-color|Color)?|border(?:-color|Color|Top|Right|Bottom|Left)?|outline(?:-color|Color)?|fill|stroke|stopColor|stop-color|floodColor|caretColor|boxShadow)\s*[:=]\s*\{?\s*(['"\x60]?)(?:[^'"\x60;}]*\s)?(?:${NAMED})\1?(?![\w-])`,
      'g',
    ),
  },
  {
    // bg-${x}-500, `text-${color}`, 'bg-' + x : classes built from fragments are invisible to the palette rule
    id: 'fragment-class',
    label: 'class name built from fragments (bg-${x}, "bg-" + x)',
    re: new RegExp(
      `${START}${VARIANTS}(?:${PREFIX})-(?:(?:${PALETTE})-)?\\$\\{|['"\\x60](?:${PREFIX})-['"\\x60]\\s*\\+`,
      'g',
    ),
  },
  { id: 'hex', label: 'hex color literal', re: /(?<![\w&#])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])/g },
  { id: 'rgb-hsl', label: 'rgb()/hsl() literal', re: /\b(?:rgba?|hsla?)\((?!\s*(?:var\(|\$\{))/g }, // rgb(${channels}) is allowed: channels come from a theme token
  {
    id: 'text-accent',
    label: 'text-accent used as text (use text-accent-text)',
    re: new RegExp(`${START}${VARIANTS}text-accent(?!-(?:text|fg|hint)${END})(?:-[\\w-]+)?${OPACITY}${END}`, 'g'),
  },
  {
    id: 'text-opacity',
    label: 'opacity modifier on text',
    re: new RegExp(`${START}${VARIANTS}text-(?!(?:${TEXT_SIZES})\\/)[\\w-]+\\/(?:\\d+|\\[[^\\]\\s]+\\])${END}`, 'g'),
  },
  {
    id: 'bg-opacity',
    label: 'bg opacity other than bg-page/NN, bg-surface/NN, bg-black/NN',
    re: new RegExp(`${START}${VARIANTS}bg-(?!(?:page|surface|black)\\/)[\\w-]+\\/(?:\\d+|\\[[^\\]\\s]+\\])${END}`, 'g'),
  },
];

// ---------------------------------------------------------------- comment stripping

// Replaces comment text with spaces (newlines kept) so line numbers stay correct.
// Handles /* */ (incl. JSX {/* */}), <!-- --> and // line comments, skipping
// string and template literals so "http://..." and class strings survive.
function stripComments(src, isHtml) {
  let out = '';
  let i = 0;
  const blank = (s) => s.replace(/[^\n]/g, ' ');
  while (i < src.length) {
    const c = src[i];
    const two = src.slice(i, i + 2);
    if (isHtml && src.startsWith('<!--', i)) {
      const end = src.indexOf('-->', i + 4);
      const j = end === -1 ? src.length : end + 3;
      out += blank(src.slice(i, j));
      i = j;
    } else if (two === '/*') {
      const end = src.indexOf('*/', i + 2);
      const j = end === -1 ? src.length : end + 2;
      out += blank(src.slice(i, j));
      i = j;
    } else if (two === '//' && !isHtml && src[i - 1] !== ':') {
      const end = src.indexOf('\n', i);
      const j = end === -1 ? src.length : end;
      out += blank(src.slice(i, j));
      i = j;
    } else if (!isHtml && (c === '"' || c === "'" || c === '`')) {
      // Copy the literal verbatim; template ${...} contents are kept as-is.
      let j = i + 1;
      while (j < src.length && src[j] !== c) {
        if (src[j] === '\\') j++;
        else if (c !== '`' && src[j] === '\n') break; // unterminated, e.g. apostrophe in JSX text
        j++;
      }
      out += src.slice(i, j + 1);
      i = j + 1;
    } else {
      out += c;
      i++;
    }
  }
  return out;
}

// ---------------------------------------------------------------- scanning

export function scanText(text, isHtml = false) {
  const findings = [];
  const lines = stripComments(text, isHtml).split('\n');
  const origLines = text.split('\n');
  lines.forEach((line, idx) => {
    // theme-exempt usually sits in a comment, which was blanked; count markers on the original line.
    // Each marker exempts one finding (the first on the line); any further raw color is still reported.
    const original = origLines[idx] ?? '';
    const markers = (original.match(/theme-exempt/g) ?? []).length;
    const taken = [];
    const lineFindings = [];
    for (const rule of RULES) {
      for (const m of line.matchAll(rule.re)) {
        const s = m.index;
        const e = s + m[0].length;
        if (taken.some(([a, b]) => s < b && e > a)) continue;
        taken.push([s, e]);
        lineFindings.push({ rule: rule.id, line: idx + 1, match: m[0], at: s });
      }
    }
    lineFindings.sort((a, b) => a.at - b.at);
    findings.push(...lineFindings.slice(markers));
  });
  return findings;
}

function walk(dir, out = []) {
  if (!existsSync(dir)) return out;
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name.startsWith('.')) continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(jsx?|tsx?|css|svg|html)$/.test(name) && !name.endsWith('.d.ts')) out.push(p);
  }
  return out;
}

function loadAllowlist() {
  if (!existsSync(ALLOW_FILE)) return new Set();
  return new Set(
    readFileSync(ALLOW_FILE, 'utf8')
      .split('\n')
      .map((l) => l.replace(/#.*/, '').trim())
      .filter(Boolean)
      .map((l) => l.replace(/\\/g, '/').replace(/^\.\//, '')),
  );
}

function main() {
  const files = [];
  for (const app of APPS) {
    files.push(...walk(join(FRONTEND, app, 'src')));
    files.push(...walk(join(FRONTEND, app, 'public')));
    const html = join(FRONTEND, app, 'index.html');
    if (existsSync(html)) files.push(html);
  }
  const allow = loadAllowlist();
  const perFile = [];
  const totals = Object.fromEntries(RULES.map((r) => [r.id, 0]));
  const perApp = Object.fromEntries(APPS.map((a) => [a, 0]));
  let total = 0;
  let blocking = 0;

  for (const file of files.sort()) {
    if (file === THEME_CSS) continue;
    const relPath = relative(REPO, file).split(sep).join('/');
    const findings = scanText(readFileSync(file, 'utf8'), /\.(html|svg)$/.test(file));
    if (!findings.length) continue;
    const allowed = allow.has(relPath);
    const counts = {};
    for (const f of findings) {
      counts[f.rule] = (counts[f.rule] ?? 0) + 1;
      totals[f.rule]++;
    }
    total += findings.length;
    perApp[relPath.split('/')[1]] += findings.length;
    if (!allowed) blocking += findings.length;
    perFile.push({ relPath, findings, counts, allowed });
  }

  console.log(`Raw-color scan (${STRICT ? 'strict' : 'report'} mode), ${files.length} files scanned\n`);
  const w = Math.max(4, ...perFile.map((f) => f.relPath.length));
  for (const f of perFile) {
    const detail = Object.entries(f.counts).map(([k, v]) => `${k} ${v}`).join(', ');
    console.log(`${f.relPath.padEnd(w)}  ${String(f.findings.length).padStart(4)}  ${detail}${f.allowed ? '  [allowlisted]' : ''}`);
    if (VERBOSE) for (const x of f.findings) console.log(`    ${String(x.line).padStart(5)}  ${x.rule.padEnd(12)}  ${x.match}`);
  }

  console.log('\nBy category:');
  for (const r of RULES) console.log(`  ${r.id.padEnd(13)} ${String(totals[r.id]).padStart(5)}  ${r.label}`);
  console.log('\nBy app:');
  for (const a of APPS) console.log(`  ${a.padEnd(13)} ${String(perApp[a]).padStart(5)}`);
  const allowedCount = total - blocking;
  console.log(
    `\nTotal: ${total} findings in ${perFile.length} files` +
      (allowedCount ? ` (${allowedCount} in allowlisted files, ${blocking} outside)` : ''),
  );
  if (allow.size) console.log(`Allowlist: ${allow.size} entries in ${relative(REPO, ALLOW_FILE)}`);

  if (STRICT && blocking > 0) {
    console.log(`\nFAIL: ${blocking} raw colors outside the allowlist.`);
    process.exit(1);
  }
  if (STRICT) console.log('\nPASS: no raw colors outside the allowlist.');
}

if (process.argv[1] === fileURLToPath(import.meta.url)) main();
