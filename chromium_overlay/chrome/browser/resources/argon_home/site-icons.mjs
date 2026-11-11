// Site HTML is scanned as text, never inserted into the privileged document.
// Image candidates are decoded before ranking; declared sizes are only hints.
export const MAX_ICON_CANDIDATES = 12;
const MAX_IMAGE_BYTES = 1024 * 1024;
const MAX_PAGE_CHARS = 256 * 1024;
const IMAGE_MIME = /^image\/(png|jpeg|webp|gif|svg\+xml|x-icon|vnd\.microsoft\.icon)$/i;
const RAW_TEXT = new Set(['script','style','title','textarea','xmp','iframe','noembed','noframes','noscript']);

function httpURL(value, base) {
  try {
    if (typeof value !== 'string' || value.length > 16384) return '';
    const url = new URL(value, base);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return '';
    url.hash = '';
    return url.href;
  } catch { return ''; }
}

function iconURL(value, base) {
  if (typeof value === 'string' && value.length <= MAX_IMAGE_BYTES * 1.4
      && /^data:image\/(png|jpeg|webp|gif|svg\+xml|x-icon|vnd\.microsoft\.icon)(?:;[^,]*)?,/i.test(value)) return value;
  return httpURL(value, base);
}

function candidate(url, kind, sizes = '', type = '') {
  const declared = String(sizes).slice(0, 2048).toLowerCase().split(/\s+/).slice(0, 64);
  const vector = type.toLowerCase() === 'image/svg+xml' || /\.svg(?:[?#]|$)/i.test(url);
  const hint = vector || declared.includes('any') ? 8192 : Math.max(0, ...declared.map(size => {
    const dimensions = /^(\d{1,5})x(\d{1,5})$/.exec(size);
    return dimensions ? Math.min(8192, Number(dimensions[1]), Number(dimensions[2])) : 0;
  }));
  return hint ? {url, kind, hint} : {url, kind};
}

// A bounded, quote-aware tag scan excludes comments, raw text and inert
// templates. Attribute entity decoding is supplied by the browser adapter.
function* tags(html, decode) {
  html = String(html).slice(0, MAX_PAGE_CHARS);
  let index = 0, inert = 0, count = 0;
  while (index < html.length && count++ < 4096) {
    const start = html.indexOf('<', index);
    if (start < 0) return;
    if (html.startsWith('<!--', start)) {
      const end = html.indexOf('-->', start + 4);
      index = end < 0 ? html.length : end + 3;
      continue;
    }
    const match = /^<\s*(\/?)\s*([a-z][a-z0-9:-]*)\b/i.exec(html.slice(start, start + 80));
    if (!match) { index = start + 1; continue; }
    let end = start + match[0].length, quote = '';
    for (; end < html.length; end++) {
      const ch = html[end];
      if (quote) { if (ch === quote) quote = ''; }
      else if (ch === '"' || ch === "'") quote = ch;
      else if (ch === '>') break;
    }
    if (end === html.length) return;
    index = end + 1;
    const name = match[2].toLowerCase(), closing = !!match[1];
    if (name === 'template') { inert = Math.max(0, inert + (closing ? -1 : 1)); continue; }
    if (!closing && RAW_TEXT.has(name)) {
      const close = new RegExp('</' + name + '\\s*>', 'gi');
      close.lastIndex = index;
      const found = close.exec(html);
      index = found ? close.lastIndex : html.length;
      continue;
    }
    if (closing || inert || !['link', 'base'].includes(name)) continue;
    const attrs = Object.create(null);
    const text = html.slice(start + match[0].length, end);
    const pattern = /([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
    for (const attr of text.matchAll(pattern)) {
      const key = attr[1].toLowerCase();
      if (!Object.hasOwn(attrs, key)) attrs[key] = decode(attr[2] ?? attr[3] ?? attr[4] ?? '');
    }
    yield {name, attrs};
  }
}

export function pageIconCandidates(html, pageURL, decode = value => value) {
  const page = httpURL(pageURL);
  if (!page) return {icons: [], manifest: ''};
  const entries = [...tags(html, decode)];
  const declaredBase = entries.find(tag => tag.name === 'base' && Object.hasOwn(tag.attrs, 'href'));
  const base = declaredBase ? httpURL(declaredBase.attrs.href, page) || page : page;
  const icons = [], seen = new Set();
  let manifest = '';
  for (const {name, attrs} of entries) {
    if (name !== 'link' || !attrs.href) continue;
    const rel = (attrs.rel || '').toLowerCase().split(/\s+/);
    if (!manifest && rel.includes('manifest')) manifest = httpURL(attrs.href, base);
    const kind = rel.includes('icon') ? 'site'
      : rel.includes('apple-touch-icon') || rel.includes('apple-touch-icon-precomposed') ? 'touch' : '';
    if (!kind || icons.length >= 64) continue;
    const url = iconURL(attrs.href, base);
    if (!url || seen.has(url)) continue;
    seen.add(url);
    icons.push(candidate(url, kind, attrs.sizes, attrs.type));
  }
  return {icons, manifest};
}

export function manifestIconCandidates(text, manifestURL) {
  try {
    const manifest = JSON.parse(text), icons = [], seen = new Set();
    if (!Array.isArray(manifest?.icons)) return icons;
    for (const entry of manifest.icons.slice(0, 64)) {
      if (!entry || typeof entry !== 'object') continue;
      const purpose = typeof entry.purpose === 'string' ? entry.purpose.toLowerCase().split(/\s+/) : ['any'];
      if (!purpose.includes('any') && !purpose.includes('maskable')) continue;
      const url = iconURL(entry.src, manifestURL);
      if (!url || seen.has(url)) continue;
      seen.add(url); icons.push(candidate(url, 'manifest', entry.sizes,
        typeof entry.type === 'string' ? entry.type : ''));
    }
    return icons;
  } catch { return []; }
}

// Suitable square shapes first, scalable vectors next, then actual resolution.
// Order is stable for equally good results. File size is not a quality signal.
export function rankIcons(icons) {
  const key = icon => {
    const side = Math.min(icon.width, icon.height), ratio = side / Math.max(icon.width, icon.height);
    return [ratio >= .9 ? 1 : 0, icon.vector ? 1 : 0, side, ratio];
  };
  return icons.filter(icon => Number.isFinite(icon.width) && Number.isFinite(icon.height)
      && icon.width > 0 && icon.height > 0).sort((a, b) => {
    const left = key(a), right = key(b);
    for (let i = 0; i < left.length; i++) if (left[i] !== right[i]) return right[i] - left[i];
    return 0;
  });
}

let nativeBindings;
const transportQueue = [];
let activeTransports = 0;
async function readBrowserResource(kind, url, signal) {
  if (globalThis.location?.protocol !== 'chrome:' || typeof globalThis.chrome?.send !== 'function')
    throw Error('Icon discovery needs the browser transport');
  // Keep overlapping/cancelled chooser sessions below the native request cap.
  if (activeTransports >= 3) await new Promise(resolve => transportQueue.push(resolve));
  else activeTransports++;
  try {
    if (signal?.aborted) return {};
    nativeBindings ??= import('chrome://resources/js/cr.js');
    const {sendWithPromise} = await nativeBindings;
    return await sendWithPromise('getArgonIconResource', kind, url);
  } finally {
    const next = transportQueue.shift();
    if (next) next(); else activeTransports--;
  }
}

function textResource(resource, page = false) {
  if (typeof resource?.body !== 'string' || resource.body.length > MAX_PAGE_CHARS * 1.4) return '';
  try {
    const bytes = Uint8Array.from(atob(resource.body), ch => ch.charCodeAt(0));
    const meta = page ? new TextDecoder('windows-1252').decode(bytes.slice(0, 1024))
      .match(/charset\s*=\s*["']?\s*([a-z0-9_-]+)/i)?.[1] : '';
    let decoder;
    try { decoder = new TextDecoder(resource.charset || meta || 'utf-8'); }
    catch { decoder = new TextDecoder(); }
    return decoder.decode(bytes);
  } catch { return ''; }
}

export function decodeIconImage(candidate, resource) {
  return new Promise(resolve => {
    let source = candidate.url;
    if (!/^data:/i.test(source)) {
      if (!IMAGE_MIME.test(resource?.mime || '') || typeof resource.body !== 'string'
          || resource.body.length > Math.ceil(MAX_IMAGE_BYTES / 3) * 4) { resolve(null); return; }
      source = 'data:' + resource.mime.toLowerCase() + ';base64,' + resource.body;
    }
    const vector = /^data:image\/svg\+xml[;,]/i.test(source);
    const image = new Image();
    let finished = false;
    const finish = value => { if (!finished) { finished = true; clearTimeout(timer); image.onload = image.onerror = null; resolve(value); } };
    const timer = setTimeout(() => finish(null), 5000);
    image.onerror = () => finish(null);
    image.onload = () => {
      const width = image.naturalWidth, height = image.naturalHeight;
      if (!width || !height || width > 8192 || height > 8192 || width * height > 16777216) { finish(null); return; }
      try {
        let data;
        if (vector) {
          // Uploaded SVGs are already supported by the homepage. Keep vectors
          // local and scalable, normalizing non-base64 data URLs as well.
          const comma = source.indexOf(','), header = source.slice(0, comma);
          if (/;base64$/i.test(header)) data = 'data:image/svg+xml;base64,' + source.slice(comma + 1);
          else {
            const bytes = new TextEncoder().encode(decodeURIComponent(source.slice(comma + 1)));
            let binary = '';
            for (let offset = 0; offset < bytes.length; offset += 8192)
              binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
            data = 'data:image/svg+xml;base64,' + btoa(binary);
          }
        } else {
          const scale = Math.min(1, 256 / Math.max(width, height));
          const canvas = document.createElement('canvas');
          canvas.width = Math.max(1, Math.round(width * scale));
          canvas.height = Math.max(1, Math.round(height * scale));
          const context = canvas.getContext('2d');
          context.imageSmoothingQuality = 'high';
          context.drawImage(image, 0, 0, canvas.width, canvas.height);
          data = canvas.toDataURL('image/png');
        }
        if (data.length > MAX_IMAGE_BYTES * 1.4) { finish(null); return; }
        const mime = /^data:([^;,]+)/i.exec(source)?.[1] || '';
        const format = {'image/svg+xml':'SVG','image/x-icon':'ICO','image/vnd.microsoft.icon':'ICO','image/jpeg':'JPG','image/png':'PNG','image/webp':'WebP','image/gif':'GIF'}[mime.toLowerCase()] || '';
        finish({...candidate, data, width, height, vector, format});
      } catch { finish(null); }
    };
    image.src = source;
  });
}

export async function discoverSiteIcons(value, {readResource = readBrowserResource,
    decodeAttribute = text => text, loadImage = decodeIconImage, signal} = {}) {
  const page = httpURL(value);
  if (!page) throw Error('Invalid site address');
  const check = () => { if (signal?.aborted) throw new DOMException('Aborted', 'AbortError'); };
  const read = async (kind, url) => {
    check();
    let result;
    try { result = await readResource(kind, url, signal); } catch { result = {}; }
    check(); return result;
  };
  const response = await read('page', page);
  const finalURL = httpURL(response?.url) || page;
  const found = pageIconCandidates(textResource(response, true), finalURL, decodeAttribute);
  const candidates = [...found.icons];
  if (found.manifest) {
    const manifest = await read('manifest', found.manifest);
    candidates.push(...manifestIconCandidates(textResource(manifest), httpURL(manifest?.url) || found.manifest));
  }
  // Declared sizes help choose a bounded download set, including large PWA
  // icons after many small head icons. Final order uses decoded sizes only.
  const unique = [...new Map(candidates.map(icon => [icon.url, icon])).values()]
    .sort((a,b)=>(b.hint||0)-(a.hint||0)).slice(0, MAX_ICON_CANDIDATES - 1);
  const fallback = new URL('/favicon.ico', finalURL).href;
  if (!unique.some(icon => icon.url === fallback)) unique.push({url: fallback, kind: 'default'});
  const decoded = [];
  let index = 0;
  async function worker() {
    while (index < unique.length) {
      check();
      const position = index++, candidate = unique[position];
      const resource = /^data:/i.test(candidate.url) ? {} : await read('image', candidate.url);
      check();
      const icon = await loadImage(candidate, resource);
      check(); if (icon) decoded[position] = icon;
    }
  }
  await Promise.all(Array.from({length: Math.min(3, unique.length)}, worker));
  // Preserve the existing service as a last resort, not an extra request on
  // every successful discovery. No full page URL is sent to the service.
  if (!decoded.some(Boolean)) {
    const candidate = {url: 'https://icons.duckduckgo.com/ip3/' + new URL(finalURL).hostname + '.ico', kind: 'service'};
    const icon = await loadImage(candidate, await read('image', candidate.url));
    check(); if (icon) decoded.push(icon);
  }
  const distinct = new Map();
  for (const icon of decoded.filter(Boolean)) {
    const key = icon.data || icon.url;
    if (!distinct.has(key)) distinct.set(key, icon);
  }
  return rankIcons([...distinct.values()]);
}
