#!/usr/bin/env node
/**
 * Validate the dashboard's JSX before it can white-screen production.
 *
 *     node scripts/check_jsx.js
 *
 * WHY THIS EXISTS AND WHY IT READS THE PARSED STRING. The whole React app is a
 * Python triple-quoted string in aurora_html.py compiled by Babel-standalone in
 * the browser, with no bundler and no build step — so a syntax error is not
 * caught by anything until a user loads the page and gets a white screen.
 *
 * The subtle half: Python processes escapes before the browser ever sees the
 * source, so `split('\n')` written with a single backslash becomes a real
 * newline in the delivered string and breaks a JS string literal. Reading the
 * .py file directly would validate text the browser never receives and pass
 * happily. So this asks PYTHON for the rendered DASHBOARD_HTML and checks
 * that — the exact bytes that will be served.
 */
const { execFileSync } = require('child_process');
const path = require('path');
const babel = require('@babel/core');
const preset = require('@babel/preset-react');

const REPO = path.resolve(__dirname, '..');

let html;
try {
  html = execFileSync('python3', ['-c',
    'import sys; sys.path.insert(0, "' + REPO + '");' +
    'from src.dashboard.aurora_html import DASHBOARD_HTML;' +
    'sys.stdout.write(DASHBOARD_HTML)'
  ], { cwd: REPO, maxBuffer: 64 * 1024 * 1024, encoding: 'utf8',
       stdio: ['ignore', 'pipe', 'ignore'] });
} catch (e) {
  console.error('could not render DASHBOARD_HTML:', e.message);
  process.exit(1);
}

const parts = html.split('<script type="text/babel">');
if (parts.length < 2) {
  console.error('NO BABEL BLOCK FOUND — has the dashboard stopped shipping JSX?');
  process.exit(1);
}

let blocks = 0;
for (let i = 1; i < parts.length; i++) {
  const body = parts[i].split('</script>')[0];
  try {
    babel.transformSync(body, {
      presets: [preset], filename: `block${i}.jsx`,
      configFile: false, babelrc: false,
    });
    blocks++;
  } catch (err) {
    console.error(`BLOCK ${i} FAILED TO COMPILE:\n${err.message}`);
    process.exit(1);
  }
}
console.log(`JSX OK — ${blocks} babel block(s) parsed and transformed`);
