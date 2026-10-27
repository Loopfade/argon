// WebUI renderers cannot fetch external images. Chromium's image source fetches
// in the browser process and decodes/re-encodes in an isolated utility process.
export function imageSource(value, protocol) {
  if (/^data:image\//i.test(value)) return value;
  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol)) return '';
    if (protocol !== 'chrome:') return url.href;
    url.protocol = 'https:';
    return 'chrome://image?url=' + encodeURIComponent(url.href) + '&staticEncode=true';
  } catch { return ''; }
}

export function faviconSources(value, protocol) {
  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol)) return [];
    return [
      new URL('/favicon.ico', url.origin).href,
      'https://icons.duckduckgo.com/ip3/' + url.hostname + '.ico'
    ].map(source => imageSource(source, protocol));
  } catch { return []; }
}
