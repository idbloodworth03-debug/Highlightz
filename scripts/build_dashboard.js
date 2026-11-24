#!/usr/bin/env node
/**
 * Compile the dashboard's JSX once, here, instead of in every visitor's browser.
 *
 *     node scripts/build_dashboard.js           # write src/dashboard/static/dashboard.js
 *     node scripts/build_dashboard.js --check   # exit 1 if that file is stale
 *
 * Audit, 2026-11-24: every dashboard load downloaded Babel (about 3 MB) and
 * compiled about 600 KB of JSX in the browser before anything showed, about
 * five seconds even on a fast machine. The page now loads this file instead
 * (src/dashboard/bundle.py), and falls back to the old in-browser compile if
 * the file does not match the JSX it was built from.
 *
 * SAME COMPILER, SAME SETTINGS. @babel/standalone 7.29.0 is the exact version
 * the page loads from unpkg, and the options below are its own defaults for a
 * <script type="text/babel"> (presets react + env, the same three plugins), so
 * the code that runs is the code that ran. Only the inline source map and the
 * comments are left out.
 *
 * Run it after ANY change to the JSX in src/dashboard/aurora_html.py:
 * tests/test_dashboard_bundle.py fails until you do. It reads the JSX the way
 * check_jsx.js does, from Python's rendered DASHBOARD_HTML, so escapes are the
 * ones the browser receives.
 */
const { execFileSync } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const Babel = require('@babel/standalone');

const REPO = path.resolve(__dirname, '..');
const OUT = path.join(REPO, 'src', 'dashboard', 'static', 'dashboard.js');
const OPEN = '<script type="text/babel">';

const html = execFileSync('python3', ['-c',
  'import sys; sys.path.insert(0, "' + REPO + '");' +
  'from src.dashboard.aurora_html import DASHBOARD_HTML;' +
  'sys.stdout.buffer.write(DASHBOARD_HTML.encode("utf-8"))'
], { cwd: REPO, maxBuffer: 64 * 1024 * 1024, stdio: ['ignore', 'pipe', 'ignore'] }).toString('utf8');

const at = html.indexOf(OPEN);
if (at < 0) { console.error('NO BABEL BLOCK FOUND in DASHBOARD_HTML'); process.exit(1); }
const jsx = html.slice(at + OPEN.length, html.indexOf('</script>', at));
const hash = crypto.createHash('sha256').update(jsx, 'utf8').digest('hex');

if (process.argv.includes('--check')) {
  const have = fs.existsSync(OUT) ? (fs.readFileSync(OUT, 'utf8').slice(0, 600).match(/source-sha256: ([0-9a-f]{64})/) || [])[1] : null;
  if (have === hash) { console.log('dashboard.js is up to date'); process.exit(0); }
  console.error('dashboard.js is STALE: run node scripts/build_dashboard.js'); process.exit(1);
}

if (Babel.version !== '7.29.0') { console.error('expected @babel/standalone 7.29.0, have ' + Babel.version); process.exit(1); }
const out = Babel.transform(jsx, {
  filename: 'dashboard.jsx',
  presets: ['react', 'env'],
  plugins: ['transform-class-properties', 'transform-object-rest-spread', 'transform-flow-strip-types'],
  sourceMaps: false, comments: false, compact: true,
}).code;
const header = '/* Built by scripts/build_dashboard.js from the JSX in src/dashboard/aurora_html.py. Do not edit.\n'
             + '   source-sha256: ' + hash + ' */\n';
fs.writeFileSync(OUT, header + out + '\n');
console.log('wrote ' + path.relative(REPO, OUT) + ': ' + Math.round((header.length + out.length) / 1024) + ' KB from '
            + Math.round(jsx.length / 1024) + ' KB of JSX, source ' + hash.slice(0, 12));
