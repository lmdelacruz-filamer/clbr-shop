// Builds store/static/store/css/site.css (the pre-built Tailwind styles the site loads).
// Needs Tailwind v4 + esbuild. Set TAILWIND_DIR / ESBUILD_DIR if they are not in /opt/npm-tools/node_modules.
// Run again after ANY change to templates, store/static/store/js/*.js or store/*.py (the test-suite fails if you forget).
import fs from 'fs'; import path from 'path'; import crypto from 'crypto'; import { pathToFileURL } from 'url';
const NM = process.env.NM_DIR || '/opt/npm-tools/node_modules';
const TW = process.env.TAILWIND_DIR || path.join(NM, 'tailwindcss');
const { compile } = await import(pathToFileURL(path.join(TW, 'dist/lib.mjs')));
const esbuild = await import(pathToFileURL(path.join(process.env.ESBUILD_DIR || path.join(NM, 'esbuild'), 'lib/main.js')));
const repo = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const store = path.join(repo, 'store');

// SAME file list + hash as store/tests.py::CssFreshnessTests (keep them in sync)
export function sourceFiles() {
  const out = [];
  (function walk(d) {
    for (const f of fs.readdirSync(d).sort()) {
      const p = path.join(d, f), s = fs.statSync(p);
      if (s.isDirectory()) { if (['migrations', '__pycache__', 'img', 'vendor', 'fonts', 'css', 'management'].includes(f)) continue; walk(p); }
      else if (/\.(html|js|py)$/.test(f) && f !== 'tests.py') out.push(p);
    }
  })(store);
  const rel = (f) => path.relative(repo, f).split(path.sep).join('/');
  return out.sort((x, y) => (rel(x) < rel(y) ? -1 : rel(x) > rel(y) ? 1 : 0));
}
const files = sourceFiles();
const hash = crypto.createHash('sha256');
for (const f of files) hash.update(path.relative(repo, f).split(path.sep).join('/') + '\0' + fs.readFileSync(f) + '\0');
const digest = hash.digest('hex').slice(0, 16);

const src = files.map(f => fs.readFileSync(f, 'utf8')).join('\n');
// Two passes: keep ( ) , inside tokens (so pb-[max(0.75rem,env(safe-area-inset-bottom))] survives), and also split on them.
const candidates = new Set([...src.split(/[\s"'`<>=;{}]+/), ...src.split(/[\s"'`<>=(){};,]+/)].filter(Boolean));
const input = `@import "tailwindcss";
@theme { --color-ink:#0a0a0a; --color-coal:#141414; --color-ash:#2a2a2a; --color-bone:#ecebe4; --color-blood:#e5322d;
  --font-mono: "Space Mono", ui-monospace, monospace; --font-gothic: "English Towne", "Pirata One", serif; }
/* Keep the look the site had under Tailwind v3 */
@layer base {
  *, ::after, ::before, ::backdrop, ::file-selector-button { border-color: var(--color-gray-200, currentcolor); }
  button:not(:disabled), [role="button"]:not(:disabled) { cursor: pointer; }
}`;
const c = await compile(input, { base: TW, loadStylesheet: async (id, base) => {
  const p = id === 'tailwindcss' ? path.join(TW, 'index.css') : path.resolve(base, id);
  return { path: p, base: path.dirname(p), content: fs.readFileSync(p, 'utf8') }; } });
const min = esbuild.transformSync(c.build([...candidates]), { loader: 'css', minify: true }).code;
const outFile = path.join(store, 'static/store/css/site.css');
fs.writeFileSync(outFile, `/* clbr-src-hash:${digest} | Tailwind ${JSON.parse(fs.readFileSync(path.join(TW, 'package.json'))).version} | built by tools/build_css.mjs */\n` + min);
console.log(`site.css ${(fs.statSync(outFile).size / 1024).toFixed(1)} KB from ${files.length} files, hash ${digest}`);
