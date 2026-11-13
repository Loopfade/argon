import {t,language} from './i18n.js';

const BING='https://www.bing.com';
const MAX_RESOURCE_BYTES=1024*1024;
let bindings;

function bingURL(value,image=false) {
  try {
    if(typeof value!=='string'||!value||value.length>16384)return '';
    const url=new URL(value,BING);
    if(url.origin!==BING||url.username||url.password)return '';
    if(image&&(url.pathname!=='/th'||!/^OHR\.[a-z0-9_-]+\.jpg$/i.test(url.searchParams.get('id')||'')))return '';
    return url.href;
  } catch{return '';}
}

export function parseBingWallpapers(value) {
  if(!Array.isArray(value?.images))throw Error(t("Не удалось загрузить фоны Bing. Повторите попытку."));
  const seen=new Set();
  return value.images.slice(0,8).flatMap(item=>{
    const url=bingURL(item.url,true);
    if(!url||seen.has(url))return [];
    seen.add(url);
    const image=new URL(url),id=image.searchParams.get('id');
    const thumbnail=new URL('/th',BING);thumbnail.searchParams.set('id',id.replace(/(?:_\d+x\d+)?\.jpg$/,'_640x360.jpg'));
    return [{url,thumbnail:thumbnail.href,title:String(item.title||item.copyright||'Bing').slice(0,300),
      credit:String(item.copyright||'Bing').slice(0,1000),creditUrl:bingURL(item.copyrightlink)||BING,
      date:/^\d{8}$/.test(item.startdate)?item.startdate:'',downloadable:item.wp===true}];
  });
}

async function readBrowserResource(kind,url) {
  if(globalThis.location?.protocol!=='chrome:'||typeof globalThis.chrome?.send!=='function')
    throw Error(t("Не удалось загрузить фоны Bing. Повторите попытку."));
  // Reuse the existing bounded, cookie-free homepage resource transport.
  bindings??=import('chrome://resources/js/cr.js');
  return (await bindings).sendWithPromise('getArgonIconResource',kind,url);
}

function bytes(resource,max=MAX_RESOURCE_BYTES) {
  if(typeof resource?.body!=='string'||resource.body.length>Math.ceil(max*4/3)+8)throw Error(t("Не удалось загрузить изображение."));
  const data=Uint8Array.from(atob(resource.body),c=>c.charCodeAt(0));
  if(!data.length||data.length>max)throw Error(t("Не удалось загрузить изображение."));
  return data;
}

export async function loadBingWallpapers({readResource=readBrowserResource,signal}={}) {
  const url=BING+'/HPImageArchive.aspx?format=js&idx=0&n=8&mkt='+(language()==='ru'?'ru-RU':'en-US');
  const response=await readResource('manifest',url);
  if(signal?.aborted)throw new DOMException('Aborted','AbortError');
  if(!bingURL(response?.url)||new URL(response.url).pathname!=='/HPImageArchive.aspx'||!['application/json','text/plain','application/manifest+json'].includes(response?.mime))throw Error(t("Не удалось загрузить фоны Bing. Повторите попытку."));
  return parseBingWallpapers(JSON.parse(new TextDecoder().decode(bytes(response,64*1024))));
}

export async function prepareBackground(blob) {
  if(!['image/png','image/jpeg','image/webp'].includes(blob?.type)||blob.size>10*1024*1024)
    throw Error(t("Выберите PNG, JPG или WebP размером до 10 МБ."));
  const bitmap=await createImageBitmap(blob);
  try {
    if(!bitmap.width||!bitmap.height||bitmap.width*bitmap.height>64*1024*1024)throw Error(t("Изображение слишком большое."));
    const ratio=Math.min(1,2560/bitmap.width,2560/bitmap.height);
    const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(bitmap.width*ratio));canvas.height=Math.max(1,Math.round(bitmap.height*ratio));
    const context=canvas.getContext('2d');context.fillStyle='#fff';context.fillRect(0,0,canvas.width,canvas.height);context.drawImage(bitmap,0,0,canvas.width,canvas.height);
    let data;
    for(const quality of [.88,.75,.6,.45]){data=canvas.toDataURL('image/jpeg',quality);if(data.length<=2_000_000)return data;}
    throw Error(t("Изображение слишком большое."));
  } finally {bitmap.close();}
}

export async function downloadBingWallpaper(item,{readResource=readBrowserResource,signal}={}) {
  if(!item?.downloadable||!bingURL(item.url,true))throw Error(t("Это изображение недоступно для скачивания."));
  // The established transport accepts complete images up to 1 MiB. A smaller
  // Bing rendition remains available when the 1920px original exceeds it.
  for(const size of ['1920x1080','1280x720']) {
    const url=new URL(item.url);url.searchParams.set('id',url.searchParams.get('id').replace(/(?:_\d+x\d+)?\.jpg$/,'_'+size+'.jpg'));
    const resource=await readResource('image',url.href);
    if(signal?.aborted)throw new DOMException('Aborted','AbortError');
    if(!bingURL(resource?.url,true)||!['image/png','image/jpeg','image/webp'].includes(resource?.mime))continue;
    try{return await prepareBackground(new Blob([bytes(resource)],{type:resource.mime}));}catch(error){if(signal?.aborted)throw error;}
  }
  throw Error(t("Не удалось загрузить изображение."));
}

export function backgroundBlob(data) {
  if(typeof data!=='string'||!/^data:image\/jpeg;base64,[a-zA-Z0-9+/=]+$/.test(data)||data.length>2_000_000)throw Error(t("Не удалось загрузить изображение."));
  return new Blob([Uint8Array.from(atob(data.split(',')[1]),c=>c.charCodeAt(0))],{type:'image/jpeg'});
}
