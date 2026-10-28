import test from 'node:test';
import assert from 'node:assert/strict';
import {imageSource, faviconSources, engineIcon} from '../../chromium_overlay/chrome/browser/resources/argon_home/icons.mjs';

test('WebUI loads external icons only through the sanitized browser image source', () => {
  const sources = faviconSources('https://user:secret@пример.рф/path?q=private#fragment', 'chrome:');
  const targets = sources.map(source => {
    const proxy = new URL(source);
    assert.equal(proxy.origin, 'null');
    assert.equal(proxy.protocol, 'chrome:');
    assert.equal(proxy.hostname, 'image');
    assert.equal(proxy.searchParams.get('staticEncode'), 'true');
    return proxy.searchParams.get('url');
  });
  assert.deepEqual(targets, [
    'https://xn--e1afmkfd.xn--p1ai/favicon.ico',
    'https://icons.duckduckgo.com/ip3/xn--e1afmkfd.xn--p1ai.ico'
  ]);
});

test('web mockup requests the site icon and retains a service fallback', () => {
  assert.deepEqual(faviconSources('https://example.com/a/b?x=1', 'https:'), [
    'https://example.com/favicon.ico', 'https://icons.duckduckgo.com/ip3/example.com.ico'
  ]);
});

test('custom remote icons use the same WebUI proxy and HTTPS while uploaded data stays local', () => {
  const source = new URL(imageSource('http://example.com/icon.png?size=64&x=1', 'chrome:'));
  assert.equal(source.searchParams.get('url'), 'https://example.com/icon.png?size=64&x=1');
  const data = 'data:image/png;base64,iVBORw0KGgo=';
  assert.equal(imageSource(data, 'chrome:'), data);
  for (const url of ['javascript:alert(1)', 'file:///private.png', 'chrome://settings/', 'broken']) {
    assert.equal(imageSource(url, 'chrome:'), '');
    assert.deepEqual(faviconSources(url, 'chrome:'), []);
  }
});


test('search engine icons use official site favicons consistently and support custom hosts',()=>{
  const google=engineIcon('https://www.google.com/search?q=x','chrome:');
  const duck=engineIcon('https://duckduckgo.com/?q=x','chrome:');
  const bing=engineIcon('https://www.bing.com/search?q=x','chrome:');
  for(const icon of [google,duck,bing])assert.match(icon,/^data:image\//);
  assert.equal(engineIcon('https://noai.duckduckgo.com/?q=x','chrome:'),duck);
  assert.notEqual(google,duck);assert.notEqual(bing,google);
  assert.equal(engineIcon('https://search.example/?q=x','chrome:'),faviconSources('https://search.example/','chrome:')[0]);
  assert.equal(engineIcon('https://google.com.other.example/','chrome:'),faviconSources('https://google.com.other.example/','chrome:')[0]);
  assert.equal(engineIcon('invalid','chrome:'),'');
});
