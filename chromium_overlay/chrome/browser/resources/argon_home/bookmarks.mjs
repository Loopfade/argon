import {t} from './i18n.js';

export const MAX_BOOKMARK_BYTES = 10 * 1024 * 1024;
const MAX_NODES = 20000;
const MAX_DEPTH = 64;
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const title = value => typeof value === 'string' ? value.replace(/\s+/g, ' ').trim() : '';
const invalid = () => new Error(t("Не удалось прочитать закладки. Выберите JSON или HTML, экспортированный браузером."));

function webAddress(value) {
  if (typeof value !== 'string' || value.length > 16384) return null;
  try {
    const url = new URL(value.trim());
    return ['http:', 'https:'].includes(url.protocol) && url.hostname ? url.href : null;
  } catch { return null; }
}

function collector(format) {
  const result = {format, records: [], skipped: 0};
  return {result, add(name, address, path) {
    const url = webAddress(address);
    if (!url) { result.skipped++; return; }
    result.records.push({name: title(name) || new URL(url).hostname, url, path: [...path]});
  }};
}

function parseJson(source) {
  let data;
  try { data = JSON.parse(source); } catch { throw invalid(); }
  if (data?.app === 'argon-start') throw new Error(t("Это дамп Argon. Используйте «Резервная копия → Восстановить»."));
  let roots;
  if (Array.isArray(data)) roots = data;
  else if (object(data?.roots)) roots = Object.values(data.roots);
  else if (Array.isArray(data?.bookmarks)) roots = data.bookmarks;
  else if (object(data) && Array.isArray(data.children)) {
    roots = data.root === 'placesRoot' || data.guid === 'root________' || !title(data.name ?? data.title)
      ? data.children : [data];
  } else if (object(data) && ('url' in data || 'uri' in data)) roots = [data];
  else throw invalid();

  const output = collector('JSON');
  const stack = roots.map(node => ({node, path: [], depth: 0})).reverse();
  let visited = 0;
  while (stack.length) {
    const {node, path, depth} = stack.pop();
    if (++visited > MAX_NODES || depth > MAX_DEPTH) throw new Error(t("В файле слишком много записей или уровней папок."));
    if (!object(node)) { output.result.skipped++; continue; }
    if (Array.isArray(node.children)) {
      const next = [...path, title(node.name ?? node.title) || t("Закладки")];
      if (stack.length + node.children.length + visited > MAX_NODES) throw new Error(t("В файле слишком много записей или уровней папок."));
      for (let i = node.children.length - 1; i >= 0; i--) stack.push({node: node.children[i], path: next, depth: depth + 1});
    } else if (node.type === 'text/x-moz-place-separator' || node.typeCode === 3) {
      output.result.skipped++;
    } else output.add(node.name ?? node.title, node.url ?? node.uri, path);
  }
  return output.result;
}

// Browser bookmark exports escape these characters and use UTF-8 for names.
// Never attach imported HTML to the DOM: scripts, images and frames stay text.
function entities(value) {
  const named = {amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: '\u00a0'};
  return value.replace(/&(#x[0-9a-f]+|#[0-9]+|amp|lt|gt|quot|apos|nbsp);/gi, (match, key) => {
    if (key[0] !== '#') return named[key.toLowerCase()];
    const code = key[1].toLowerCase() === 'x' ? parseInt(key.slice(2), 16) : Number(key.slice(1));
    return code > 0 && code <= 0x10ffff && !(code >= 0xd800 && code <= 0xdfff)
      ? String.fromCodePoint(code) : '\ufffd';
  });
}

function* htmlTokens(source) {
  let index = 0;
  while (index < source.length) {
    if (source[index] !== '<') {
      const end = source.indexOf('<', index);
      const next = end < 0 ? source.length : end;
      yield {text: source.slice(index, next)}; index = next; continue;
    }
    if (source.startsWith('<!--', index)) {
      const end = source.indexOf('-->', index + 4);
      if (end < 0) throw invalid();
      index = end + 3; continue;
    }
    let end = index + 1, quote = null;
    for (; end < source.length; end++) {
      const character = source[end];
      if (quote) { if (character === quote) quote = null; }
      else if (character === '"' || character === "'") quote = character;
      else if (character === '>') break;
    }
    if (end === source.length) throw invalid();
    const tag = source.slice(index + 1, end);
    index = end + 1;
    const match = /^\s*(\/?)\s*([a-z][a-z0-9]*)\b/i.exec(tag);
    if (match) yield {name: match[2].toLowerCase(), closing: Boolean(match[1]), attributes: tag.slice(match[0].length)};
  }
}

function href(attributes) {
  const pattern = /([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
  for (const match of attributes.matchAll(pattern)) {
    if (match[1].toLowerCase() === 'href') return entities(match[2] ?? match[3] ?? match[4] ?? '');
  }
  return '';
}

function parseHtml(source) {
  const output = collector('HTML'), folders = [];
  let pending = null, capture = null, raw = null, sawList = false, visited = 0;
  function finish() {
    if (!capture) return;
    const name = entities(capture.text);
    if (capture.folder) pending = title(name) || t("Закладки");
    else output.add(name, capture.url, capture.path);
    capture = null;
  }
  for (const token of htmlTokens(source)) {
    if (raw) { if (token.name === raw && token.closing) raw = null; continue; }
    if (['script', 'style', 'title'].includes(token.name) && !token.closing) { raw = token.name; continue; }
    if (token.text !== undefined) { if (capture) capture.text += token.text; continue; }
    if (token.name === 'dl') {
      finish();
      if (token.closing) {
        if (!folders.length) throw invalid();
        folders.pop();
      } else {
        sawList = true;
        if (folders.length >= MAX_DEPTH) throw new Error(t("В файле слишком много записей или уровней папок."));
        const path = folders.at(-1) ?? [];
        folders.push(pending === null ? path : [...path, pending]);
      }
      pending = null;
    } else if (token.name === 'dt' && !token.closing) { finish(); pending = null; }
    else if ((token.name === 'a' || token.name === 'h3') && folders.length) {
      if (token.closing) finish();
      else {
        finish(); pending = null;
        if (++visited > MAX_NODES) throw new Error(t("В файле слишком много записей или уровней папок."));
        capture = {folder: token.name === 'h3', text: '', url: href(token.attributes), path: folders.at(-1)};
      }
    }
  }
  finish();
  if (!sawList || folders.length || raw) throw invalid();
  return output.result;
}

export function parseBookmarks(source) {
  if (typeof source !== 'string' || new TextEncoder().encode(source).byteLength > MAX_BOOKMARK_BYTES) throw new Error(t("Выберите файл закладок размером до 10 МБ."));
  source = source.replace(/^\ufeff/, '').trim();
  if (source.startsWith('mozLz40')) throw new Error(t("Сжатый JSONLZ4 не поддерживается. Экспортируйте закладки в HTML или JSON."));
  const result = source.startsWith('{') || source.startsWith('[') ? parseJson(source) : parseHtml(source);
  if (!result.records.length) throw new Error(t("В файле нет закладок с адресами http:// или https://."));
  return result;
}
