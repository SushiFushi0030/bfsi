import { readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import assert from 'node:assert/strict';

const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const demo = JSON.parse(await readFile(path.join(web, '../examples/demo/payload.json'), 'utf8'));
assert.equal(demo.analysis.synthetic, true, 'Published example must be explicitly synthetic.');
assert.ok(demo.video.title.includes('合成'), 'Demo title must identify synthetic content.');
const total = demo.video.comment_count;
assert.equal(Object.values(demo.distributions.market_stance).reduce((a, b) => a + b, 0), total);
for (const rows of Object.values(demo.representative_comments)) {
  for (const row of rows) {
    assert.match(row.comment_id, /^(?:(?:demo|synthetic)[A-Za-z0-9_-]*|[1-9][0-9]{0,3})$/, 'Published identifiers must be synthetic.');
    assert.ok(row.raw_text.includes('【合成评论】'), 'Example text must identify synthetic content.');
    assert.equal(typeof row.raw_text, 'string');
  }
}
const source = await readFile(path.join(web, 'src/main.tsx'), 'utf8');
assert.ok(source.includes('/api/sample'));
assert.ok(!source.includes('/api/history'));
assert.ok(!/377条|已验收历史样本|task 0[1-7]/.test(source));
assert.ok(source.includes('no_directional_signal'));
const publicFiles = await readdir(path.join(web, 'public'));
assert.ok(!publicFiles.includes('data'), 'Real historical payloads must not enter static assets.');
const built = await readFile(path.join(web, 'dist/index.html'), 'utf8');
assert.ok(built.includes('/assets/') || built.includes('./assets/'));
console.log(JSON.stringify({ passed: true, synthetic_example: true, comments: total, static_data_removed: true }));
