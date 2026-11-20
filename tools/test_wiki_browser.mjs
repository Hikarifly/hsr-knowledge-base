// Browser smoke test with Chrome/Edge CDP; website itself has no runtime dependencies.
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const binary = process.env.WIKI_BROWSER;
if (!binary) throw new Error('Set WIKI_BROWSER to a Chrome or Edge executable');
const profile = await mkdtemp(path.join(tmpdir(), 'hsr-wiki-browser-'));
const browser = spawn(binary, [
  '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
  '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'
], { stdio: ['ignore', 'ignore', 'pipe'] });
let browserErrors = '';
browser.stderr.setEncoding('utf8');
browser.stderr.on('data', chunk => { browserErrors = (browserErrors + chunk).slice(-4000); });
let spawnError;
browser.on('error', error => { spawnError = error; });
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
let socket;
try {
  let port;
  for (let i = 0; i < 300; i++) {
    try { port = Number((await readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]); break; }
    catch {
      if (spawnError || browser.exitCode !== null) throw new Error(`Browser exited before starting: ${spawnError || browserErrors}`);
      await wait(100);
    }
  }
  if (!port) throw new Error(`Browser did not start: ${browserErrors}`);
  const metadata = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
  socket = new WebSocket(metadata.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let id = 0;
  const pending = new Map();
  socket.onmessage = event => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
    }
  };
  function send(method, params = {}, sessionId) {
    const command = { id: ++id, method, params, ...(sessionId ? { sessionId } : {}) };
    return new Promise((resolve, reject) => {
      pending.set(command.id, { resolve, reject });
      socket.send(JSON.stringify(command));
      setTimeout(() => { if (pending.delete(command.id)) reject(new Error(`CDP timeout: ${method}`)); }, 15000).unref();
    });
  }
  const target = await send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await send('Target.attachToTarget', { targetId: target.targetId, flatten: true });
  const cdp = (method, params = {}) => send(method, params, sessionId);
  await cdp('Page.enable');
  await cdp('Runtime.enable');
  const evaluate = async expression => {
    const reply = await cdp('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (reply.exceptionDetails) throw new Error(reply.exceptionDetails.text);
    return reply.result.value;
  };
  async function navigate(url) {
    await cdp('Page.navigate', { url });
    for (let i = 0; i < 100; i++) {
      await wait(100);
      if (await evaluate('document.readyState === "complete"')) return;
    }
    throw new Error('Page load timed out');
  }
  const home = pathToFileURL(path.resolve('wiki/index.html')).href;
  for (const width of [1280, 600, 390]) {
    await cdp('Emulation.setDeviceMetricsOverride', { width, height: width === 1280 ? 800 : 900, deviceScaleFactor: 1, mobile: width < 640 });
    await navigate(home);
    const ok = await evaluate('document.documentElement.scrollWidth <= innerWidth');
    if (!ok) throw new Error(`Homepage overflows at ${width}px`);
  }
  const longQueryOk = await evaluate(`(() => {
    const box = document.querySelector('#search');
    box.value = 'x'.repeat(500);
    box.dispatchEvent(new Event('input', { bubbles: true }));
    const ok = document.querySelector('#resultmeta').textContent.includes('没有匹配') &&
      document.documentElement.scrollWidth <= innerWidth;
    box.value = '';
    box.dispatchEvent(new Event('input', { bubbles: true }));
    return ok;
  })()`);
  if (!longQueryOk) throw new Error('Long no-result query overflows the mobile viewport');
  const result = await evaluate(`(() => {
    const candidates = window.TITLES.filter(t => t.summary && t.summary.length > 12);
    for (const item of candidates) {
      const needle = item.summary.slice(2, 10);
      if (!needle || item.title.includes(needle) || item.path.includes(needle)) continue;
      const box = document.querySelector('#search');
      box.value = needle;
      box.dispatchEvent(new Event('input', { bubbles: true }));
      const result = [...document.querySelectorAll('#results li')].find(el => el.querySelector('a')?.textContent === item.title);
      if (result && result.querySelector('.result-summary')) return { needle, title: item.title };
    }
    return null;
  })()`);
  if (!result) throw new Error('No body-only summary search result was found');
  // Exercise browser keyboard input and focus order rather than synthetic DOM events.
  await evaluate(`(() => {
    const box = document.querySelector('#search');
    box.value = '';
    box.dispatchEvent(new Event('input', { bubbles: true }));
    document.activeElement.blur();
  })()`);
  async function pressKey(key, code, windowsVirtualKeyCode) {
    const params = { key, code, windowsVirtualKeyCode };
    await cdp('Input.dispatchKeyEvent', { type: 'keyDown', ...params });
    await cdp('Input.dispatchKeyEvent', { type: 'keyUp', ...params });
  }
  let focused = false;
  for (let i = 0; i < 100; i++) {
    await pressKey('Tab', 'Tab', 9);
    if (await evaluate(`document.activeElement === document.querySelector('#search')`)) { focused = true; break; }
  }
  if (!focused) throw new Error('Search input is not reachable with Tab');
  await cdp('Input.insertText', { text: '角色' });
  if (!await evaluate(`document.querySelectorAll('#results li').length > 1`)) throw new Error('Need multiple results to test keyboard wrapping');
  await pressKey('ArrowUp', 'ArrowUp', 38);
  if (!await evaluate(`document.querySelector('#search').getAttribute('aria-activedescendant') === document.querySelector('#results li:last-child').id`)) {
    throw new Error('ArrowUp from no selection did not select the last result');
  }
  await pressKey('ArrowDown', 'ArrowDown', 40);
  if (!await evaluate(`document.querySelector('#search').getAttribute('aria-activedescendant') === document.querySelector('#results li:first-child').id`)) {
    throw new Error('ArrowDown did not wrap from the last result to the first');
  }
  // Start a Chromium composition and deliver a trusted key through CDP.
  await evaluate(`window.imeProbe = null; document.querySelector('#search').addEventListener('keydown',
    e => { window.imeProbe = { key: e.key, composing: e.isComposing, trusted: e.isTrusted, prevented: e.defaultPrevented }; }, { once: true });`);
  await cdp('Input.imeSetComposition', { text: '测试', selectionStart: 2, selectionEnd: 2 });
  await pressKey('ArrowDown', 'ArrowDown', 40);
  const nativeIme = await evaluate('window.imeProbe');
  if (nativeIme?.key !== 'ArrowDown' || nativeIme.composing !== true || nativeIme.trusted !== true || nativeIme.prevented !== false) {
    throw new Error(`Browser composition key was intercepted: ${JSON.stringify(nativeIme)}`);
  }
  await cdp('Input.imeSetComposition', { text: '', selectionStart: 0, selectionEnd: 0 });
  // IME candidate selection owns these keys while composing; search must not cancel them.
  const imeOk = await evaluate(`(() => {
    const box = document.querySelector('#search');
    const selected = box.getAttribute('aria-activedescendant');
    const value = box.value;
    for (const key of ['ArrowDown', 'ArrowUp', 'Enter', 'Escape']) {
      const event = new KeyboardEvent('keydown', { key, isComposing: true, bubbles: true, cancelable: true });
      box.dispatchEvent(event);
      if (event.defaultPrevented || box.value !== value || box.getAttribute('aria-activedescendant') !== selected) return false;
    }
    const legacy = new KeyboardEvent('keydown', { key: 'Enter', keyCode: 229, bubbles: true, cancelable: true });
    box.dispatchEvent(legacy);
    return !legacy.defaultPrevented && box.value === value && box.getAttribute('aria-activedescendant') === selected;
  })()`);
  if (!imeOk) throw new Error('Search intercepted IME composition keys');
  await pressKey('Escape', 'Escape', 27);
  await cdp('Input.insertText', { text: result.needle });
  const index = await evaluate(`(() => {
    const box = document.querySelector('#search');
    box.blur();
    box.focus();
    if (box.getAttribute('aria-expanded') !== 'true') throw new Error('Results not announced after refocusing');
    return [...document.querySelectorAll('#results li')].findIndex(el => el.querySelector('a')?.textContent === ${JSON.stringify(result.title)});
  })()`);
  if (index < 0) throw new Error('Body-only result disappeared after keyboard input');
  for (let i = 0; i <= index; i++) await pressKey('ArrowDown', 'ArrowDown', 40);
  const selectedHref = await evaluate(`(() => {
    const box = document.querySelector('#search');
    const selected = document.getElementById(box.getAttribute('aria-activedescendant'));
    if (!selected || selected.querySelector('a')?.textContent !== ${JSON.stringify(result.title)}) throw new Error('Keyboard selected the wrong result');
    return selected.querySelector('a').href;
  })()`);
  await pressKey('Enter', 'Enter', 13);
  for (let i = 0; i < 100; i++) {
    await wait(100);
    if (await evaluate(`location.href === ${JSON.stringify(selectedHref)}`)) break;
    if (i === 99) throw new Error('Enter did not open the selected search result');
  }
  const article = pathToFileURL(path.resolve('wiki/pages/worldview/势力与组织.html')).href;
  for (const width of [1280, 600, 390]) {
    await cdp('Emulation.setDeviceMetricsOverride', { width, height: width === 1280 ? 800 : 900, deviceScaleFactor: 1, mobile: width < 640 });
    await navigate(article);
    const articleOk = await evaluate(`(() => {
      const details = document.querySelector('.page-toc details');
      if (!details || !details.querySelector('a')) return false;
      details.open = true;
      const link = details.querySelector('a');
      return !!document.getElementById(decodeURIComponent(link.hash.slice(1))) &&
        document.documentElement.scrollWidth <= innerWidth;
    })()`);
    if (!articleOk) throw new Error(`Article TOC or layout failed at ${width}px`);
  }
  await cdp('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
  await navigate(home);
  if (!await evaluate(`matchMedia('(prefers-reduced-motion: reduce)').matches &&
    getComputedStyle(document.querySelector('.hero-title')).animationName === 'none'`)) {
    throw new Error('Reduced-motion preference was not respected');
  }
  await cdp('Emulation.setScriptExecutionDisabled', { value: true });
  await navigate(home);
  if (!await evaluate(`!!document.querySelector('.cats a[href]') && !!document.querySelector('noscript')`)) {
    throw new Error('Category navigation is unavailable without JavaScript');
  }
  await navigate(article);
  if (!await evaluate(`!!document.querySelector('.prose') && !!document.querySelector('.page-toc a[href]')`)) {
    throw new Error('Article or TOC is unavailable without JavaScript');
  }
  console.log('PASS: summary search, keyboard navigation, article TOC, responsive layout, reduced motion and no-JavaScript browsing');
} finally {
  if (socket) socket.close();
  browser.kill();
  await wait(250);
  await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
}
