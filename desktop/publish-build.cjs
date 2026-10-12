// Keep intermediate Electron bundles separate from distributable installers.
const fs = require('node:fs');
const path = require('node:path');
const source = path.resolve(__dirname, '../.artifacts/desktop');
const target = path.resolve(__dirname, '../dist/desktop');
fs.mkdirSync(target, { recursive: true });
for (const name of fs.readdirSync(source)) {
  if (name.endsWith('.exe') || name.endsWith('.exe.blockmap')) {
    fs.copyFileSync(path.join(source, name), path.join(target, name));
  }
}
