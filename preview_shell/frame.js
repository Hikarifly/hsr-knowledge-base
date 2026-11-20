/* Trusted bridge, executed inside an opaque sandbox before untrusted content. */
(() => {
  let page = 'index.html';
  let started = false;
  let sequence = 0;
  const pending = new Map();
  const base = 'https://preview.invalid/';

  function localPath(raw) {
    try {
      const url = new URL(raw, new URL(page, base));
      if (url.origin !== base.slice(0, -1)) return null;
      return decodeURIComponent(url.pathname.slice(1));
    } catch { return null; }
  }

  function read(path, kind) {
    return new Promise(resolve => {
      const id = ++sequence;
      pending.set(id, resolve);
      parent.postMessage({type: 'preview:read', id, path, kind}, '*');
      setTimeout(() => { if (pending.delete(id)) resolve(null); }, 15000);
    });
  }

  document.addEventListener('click', event => {
    const anchor = event.target instanceof Element && event.target.closest('a[href]');
    if (!anchor) return;
    const href = anchor.getAttribute('href');
    event.preventDefault();
    if (href.startsWith('#')) {
      try { document.getElementById(decodeURIComponent(href.slice(1)))?.scrollIntoView(); } catch {}
      return;
    }
    const path = localPath(href);
    if (path) parent.postMessage({type: 'preview:navigate', path}, '*');
  }, true);

  async function render(html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    doc.querySelectorAll('base, iframe, frame, object, embed, meta[http-equiv="refresh" i]').forEach(el => el.remove());
    const assets = [...doc.querySelectorAll('script[src], link[href], img[src]')];
    if (assets.length > 512) throw new Error('页面资源过多');
    await Promise.all(assets.map(async element => {
      const tag = element.tagName.toLowerCase();
      if (tag === 'link' && !element.rel.toLowerCase().split(/\s+/).includes('stylesheet')) { element.remove(); return; }
      const attr = tag === 'link' ? 'href' : 'src';
      const path = localPath(element.getAttribute(attr));
      const kind = tag === 'script' ? 'script' : tag === 'link' ? 'style' : 'image';
      const url = path && await read(path, kind);
      if (!url) { element.remove(); return; }
      element.removeAttribute('integrity');
      element.removeAttribute('crossorigin');
      element.removeAttribute('srcset');
      element.setAttribute(attr, url);
    }));
    doc.querySelectorAll('[srcset]').forEach(element => element.removeAttribute('srcset'));
    for (const node of [...doc.head.childNodes]) document.head.appendChild(document.importNode(node, true));
    document.body.replaceChildren(...[...doc.body.childNodes].map(node => document.importNode(node, true)));
    // Scripts parsed by DOMParser are inert; execute them only in this sandbox.
    for (const old of [...document.querySelectorAll('script[data-preview-source], head script:not([data-bridge]), body script')]) {
      if (!old.parentNode) continue;
      const script = document.createElement('script');
      for (const attr of old.attributes) script.setAttribute(attr.name, attr.value);
      script.async = false;
      script.textContent = old.textContent;
      if (script.src) {
        await new Promise(resolve => { script.onload = resolve; script.onerror = resolve; old.replaceWith(script); });
      } else old.replaceWith(script);
    }
    document.dispatchEvent(new Event('DOMContentLoaded', {bubbles: true}));
    parent.postMessage({type: 'preview:rendered'}, '*');
  }

  addEventListener('message', event => {
    if (event.source !== parent || !event.data || typeof event.data !== 'object') return;
    const message = event.data;
    if (message.type === 'preview:result' && Number.isSafeInteger(message.id) && pending.has(message.id)) {
      const resolve = pending.get(message.id);
      pending.delete(message.id);
      resolve(typeof message.url === 'string' ? message.url : null);
    } else if (message.type === 'preview:init' && !started && typeof message.html === 'string' && typeof message.page === 'string') {
      started = true;
      page = message.page;
      render(message.html).catch(() => parent.postMessage({type: 'preview:error'}, '*'));
    }
  });
  parent.postMessage({type: 'preview:ready'}, '*');
})();
