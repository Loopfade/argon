export const MAX_DUMP_BYTES = 10 * 1024 * 1024;
const SCHEMA_VERSION = 2;
const BUILTIN_ENGINES = new Set(['google', 'yandex', 'duck', 'noai']);
const ICONS = new Set(['google', 'yandex', 'duck', 'youtube', 'github', 'mail', 'music', 'figma', 'notion', 'book', 'wiki', 'globe']);

export function initialState() {
  return {schemaVersion: SCHEMA_VERSION, theme: 'system', engine: 'google', customEngines: [], showLabels: true, newTab: false, shortcuts: []};
}

function object(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function text(value, label, max = 40) {
  if (typeof value !== 'string' || !value.trim() || value.length > max) throw new Error(`Некорректное поле «${label}» в дампе.`);
  return value;
}

function identifier(value) {
  if (typeof value !== 'string' || !/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$/.test(value)) throw new Error('Некорректный идентификатор в дампе.');
  return value;
}

function webUrl(value) {
  text(value, 'Адрес', 16384);
  let url;
  try { url = new URL(value); } catch { throw new Error('В дампе есть некорректный адрес сайта.'); }
  if (!['http:', 'https:'].includes(url.protocol) || !url.hostname) throw new Error('Адреса сайтов в дампе должны начинаться с http:// или https://.');
  return value;
}

function customIcon(value) {
  if (typeof value !== 'string' || value.length > 1500000) throw new Error('Некорректная или слишком большая иконка в дампе.');
  if (/^data:image\/(png|jpeg|webp|gif|svg\+xml);base64,[a-zA-Z0-9+/=\r\n]+$/.test(value)) return value;
  return webUrl(value);
}

export function validateState(raw) {
  if (!object(raw) || raw.schemaVersion !== SCHEMA_VERSION) throw new Error('Версия настроек в дампе не поддерживается.');
  if (!['system', 'light', 'dark'].includes(raw.theme) || typeof raw.showLabels !== 'boolean' || typeof raw.newTab !== 'boolean') throw new Error('Некорректные настройки оформления в дампе.');
  if (!Array.isArray(raw.customEngines) || raw.customEngines.length > 100 || !Array.isArray(raw.shortcuts)) throw new Error('Некорректный список ярлыков или поисковиков в дампе.');
  const engineIds = new Set(BUILTIN_ENGINES);
  const customEngines = raw.customEngines.map(engine => {
    if (!object(engine)) throw new Error('Некорректная поисковая система в дампе.');
    const id = identifier(engine.id);
    if (engineIds.has(id)) throw new Error('В дампе повторяются поисковые системы.');
    engineIds.add(id);
    const url = webUrl(engine.url);
    if (!url.includes('{query}')) throw new Error('В адресе своего поисковика отсутствует {query}.');
    const result = {id, name: text(engine.name, 'Название поисковика'), url};
    if (engine.icon !== undefined) {
      if (!ICONS.has(engine.icon)) throw new Error('Неизвестная иконка поисковика в дампе.');
      result.icon = engine.icon;
    }
    return result;
  });
  if (!engineIds.has(raw.engine)) throw new Error('Выбранный поисковик отсутствует в дампе.');
  const ids = new Set();
  function shortcut(item, inFolder = false) {
    if (!object(item)) throw new Error('Некорректный ярлык в дампе.');
    const id = identifier(item.id);
    if (ids.has(id)) throw new Error('В дампе повторяются идентификаторы ярлыков.');
    ids.add(id);
    if (ids.size > 2000) throw new Error('В одном дампе поддерживается до 2000 ярлыков и папок.');
    const name = text(item.name, 'Название ярлыка');
    if (item.type === 'folder') {
      if (inFolder || !Array.isArray(item.children)) throw new Error('Некорректная структура папок в дампе.');
      return {id, name, type: 'folder', children: item.children.map(child => shortcut(child, true))};
    }
    if (item.type !== undefined && item.type !== 'site') throw new Error('Неизвестный тип ярлыка в дампе.');
    const result = {id, name, url: webUrl(item.url)};
    if (item.iconMode !== undefined) {
      if (!['favicon', 'custom'].includes(item.iconMode)) throw new Error('Некорректный режим иконки в дампе.');
      result.iconMode = item.iconMode;
    }
    if (item.icon !== undefined) {
      if (!ICONS.has(item.icon)) throw new Error('Неизвестная иконка в дампе.');
      result.icon = item.icon;
    }
    if (item.customIcon !== undefined) result.customIcon = customIcon(item.customIcon);
    return result;
  }
  return {schemaVersion: SCHEMA_VERSION, theme: raw.theme, engine: raw.engine, customEngines, showLabels: raw.showLabels, newTab: raw.newTab, shortcuts: raw.shortcuts.map(item => shortcut(item))};
}

// These IDs belong to the first mockup. Only unchanged examples are removed.
const legacySites = new Map([
  ['yandex-site', ['Яндекс', 'https://ya.ru', 'yandex', 'favicon']],
  ['youtube', ['YouTube', 'https://youtube.com', 'youtube', 'favicon']],
  ['github', ['GitHub', 'https://github.com', 'github', 'favicon']],
  ['mail', ['Почта', 'https://mail.google.com', 'mail', 'favicon']],
  ['music', ['Музыка', 'https://music.yandex.ru', 'music', 'custom']],
  ['figma', ['Figma', 'https://figma.com', 'figma', 'favicon']],
  ['notion', ['Notion', 'https://notion.so', 'notion', 'favicon']],
  ['work-git', ['GitHub', 'https://github.com', 'github', 'favicon']],
  ['wiki', ['Википедия', 'https://ru.wikipedia.org', 'wiki', 'favicon']],
  ['habr', ['Хабр', 'https://habr.com', 'book', 'favicon']]
]);
const legacyFolders = new Map([['work', 'Работа'], ['read', 'Читать']]);

function removeLegacyShortcuts(shortcuts) {
  return shortcuts.flatMap(item => {
    if (!object(item)) return [item];
    if (item.type === 'folder') {
      if (!Array.isArray(item.children)) return [item];
      const children = removeLegacyShortcuts(item.children);
      if (legacyFolders.get(item.id) === item.name && children.length === 0) return [];
      return [{...item, children}];
    }
    const example = legacySites.get(item.id);
    let sameUrl = false;
    try { sameUrl = example && new URL(item.url).href === new URL(example[1]).href; } catch {}
    if (example && item.name === example[0] && sameUrl && item.icon === example[2] && item.iconMode === example[3] && !item.customIcon) return [];
    return [item];
  });
}

export function loadStoredState(raw) {
  if (!object(raw) || !Array.isArray(raw.shortcuts)) throw new Error('Настройки повреждены.');
  if (raw.schemaVersion !== undefined && raw.schemaVersion !== 1 && raw.schemaVersion !== SCHEMA_VERSION) throw new Error('Версия настроек не поддерживается.');
  const migrated = raw.schemaVersion !== SCHEMA_VERSION;
  const state = validateState({...initialState(), ...raw, schemaVersion: SCHEMA_VERSION, shortcuts: migrated ? removeLegacyShortcuts(raw.shortcuts) : raw.shortcuts});
  return {state, migrated};
}

export function createDump(state, date = new Date()) {
  return {app: 'argon-start', version: 1, createdAt: date.toISOString(), state: validateState(state)};
}

export function parseDump(source) {
  if (typeof source !== 'string' || source.length > MAX_DUMP_BYTES) throw new Error('Выберите дамп JSON размером до 10 МБ.');
  let dump;
  try { dump = JSON.parse(source); } catch { throw new Error('Не удалось прочитать JSON. Выберите целый файл дампа Argon.'); }
  if (!object(dump) || dump.app !== 'argon-start') throw new Error('Это не дамп домашней страницы Argon.');
  if (dump.version !== 1) throw new Error('Эта версия дампа не поддерживается.');
  if (typeof dump.createdAt !== 'string' || !Number.isFinite(Date.parse(dump.createdAt))) throw new Error('В дампе отсутствует корректная дата создания.');
  return {app: dump.app, version: dump.version, createdAt: dump.createdAt, state: validateState(dump.state)};
}

export function restoreState(storage, key, nextState) {
  const validated = validateState(nextState);
  // setItem is atomic. The caller updates the screen only after this succeeds.
  storage.setItem(key, JSON.stringify(validated));
  return validated;
}

export function dumpStats(state) {
  let sites = 0, folders = 0;
  state.shortcuts.forEach(item => { if (item.type === 'folder') { folders++; sites += item.children.length; } else sites++; });
  return {sites, folders, engines: state.customEngines.length};
}
