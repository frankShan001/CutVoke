import ts from "typescript";
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
export const localeRoot = join(appRoot, "src/i18n/locales");
const locales = ["zh-CN", "zh-TW", "en", "ja", "ko", "es", "fr", "de", "pt-BR", "ru", "ar"];
const walk = dir => readdirSync(dir, { withFileTypes: true }).flatMap(item =>
  item.isDirectory() ? walk(join(dir, item.name)) : /\.tsx?$/.test(item.name) ? [join(dir, item.name)] : []);

export function sourceMessages() {
  const messages = new Set();
  for (const file of walk(join(appRoot, "src"))) {
    const ast = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true);
    function visit(node) {
      if (ts.isCallExpression(node) && ["tr", "msg"].includes(node.expression.getText(ast))) {
        const first = node.arguments[0];
        if (!first || !ts.isStringLiteralLike(first)) {
          throw new Error(`${file}: UI message IDs must be authored literals; pass user data as values.`);
        }
        messages.add(first.text);
      }
      ts.forEachChild(node, visit);
    }
    visit(ast);
  }
  return [...messages].sort();
}
export const placeholders = text => [...text.matchAll(/\{\d+\}/g)].map(match => match[0]).sort();

export function checkCatalogs() {
  const keys = sourceMessages();
  const expected = new Set(keys);
  const errors = [];
  const counts = {};
  for (const locale of locales) {
    const catalog = JSON.parse(readFileSync(join(localeRoot, `${locale}.json`), "utf8"));
    counts[locale] = Object.keys(catalog).length;
    for (const key of keys) {
      const text = catalog[key];
      if (typeof text !== "string" || !text.trim()) errors.push(`${locale}: missing ${key}`);
      else if (JSON.stringify(placeholders(key)) !== JSON.stringify(placeholders(text))) errors.push(`${locale}: placeholder mismatch ${key}`);
      else if (locale === "zh-CN" && text !== key) errors.push(`Source text changed: ${key}`);
    }
    for (const key of Object.keys(catalog)) if (!expected.has(key)) errors.push(`${locale}: unused ${key}`);
  }
  return { messages: keys.length, counts, errors };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv.includes("--extract")) {
    writeFileSync(join(localeRoot, "zh-CN.json"), JSON.stringify(Object.fromEntries(sourceMessages().map(key => [key, key])), null, 2) + "\n");
    console.log("Updated authored source catalog; add matching translations to every locale.");
  } else {
    const result = checkCatalogs();
    console.log(JSON.stringify(result, null, 2));
    if (result.errors.length) process.exitCode = 1;
  }
}
