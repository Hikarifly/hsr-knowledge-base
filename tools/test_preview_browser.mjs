import { createServer } from 'node:http';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm, mkdir, writeFile, copyFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

const browserPath = process.env.WIKI_BROWSER;
if (!browserPath) throw new Error('Set WIKI_BROWSER to a Chrome or Edge executable');
const head = 'a'.repeat(40), base = 'b'.repeat(40);
const objects = new Map(), files = {};
function add(name, data) {
  const bytes = Buffer.from(data);
  const hash = createHash('sha256').update(bytes).digest('hex');
  files[name] = hash;
  objects.set(hash, JSON.stringify({data: bytes.toString('base64')}));
}
add('index.html', `<!doctype html><html><head><title>Preview</title></head><body><h1 id="content">Home</h1><a id="next" href="next.html">Next</a><img src="/canary"><script>
try { parent.document.getElementById('review-label').textContent = 'forged'; } catch { document.body.dataset.isolated = 'yes'; }
fetch('/canary').catch(() => {});
parent.postMessage({type:'test:home', isolated:document.body.dataset.isolated === 'yes'}, '*');
setTimeout(() => document.querySelector('#next').click(), 500);
parent.postMessage({type:'review:approved'}, '*');
</script></body></html>`);
const manifest = {version: 1, repo:'ganmayou2333/hsr-knowledge-base', pr:37, head, base, run:1, files};
let canaries = 0, stale = false, delayNextPr = false;
let reviewState = 'APPROVED';
const review = {id: 1, state: 'APPROVED', user: {login:'Reviewer',type:'User'}, commit_id:head, submitted_at:'2026-01-01T00:00:00Z'};
let captured = Math.floor(Date.now() / 1000);
const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/canary') { canaries++; res.writeHead(204).end(); return; }
    if (url.pathname === '/previews/37/manifest.json') { res.setHeader('Content-Type','application/json'); res.end(JSON.stringify(manifest)); return; }
    if (url.pathname === '/previews/37/review.json') { res.setHeader('Content-Type','application/json'); res.end(JSON.stringify({repo:manifest.repo,pr:37,head,approved:true,captured_at:captured,reviews:[[1,'APPROVED','Reviewer','User',head,review.submitted_at]]})); return; }
    const match = /^\/previews\/37\/objects\/([0-9a-f]{64})\.json$/.exec(url.pathname);
    if (match && objects.has(match[1])) { res.setHeader('Content-Type','application/json'); res.end(objects.get(match[1])); return; }
    const names = {'/':'index.html', '/shell.js':'shell.js', '/frame.js':'frame.js', '/shell.css':'shell.css'};
    const name = names[url.pathname];
    if (!name) { res.writeHead(404).end(); return; }
    res.setHeader('Content-Type', name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html');
    let content = await readFile(path.join('preview_shell', name), 'utf8');
    if (name === 'index.html' && process.argv.includes('--without-frame-policy')) {
      content = content.replace(/\s*<meta http-equiv="Content-Security-Policy" content="frame-src 'none'">/, '');
    }
    res.end(content);
  } catch { res.writeHead(500).end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
add('next.html', `<!doctype html><html><body><h1 id="content">Next page</h1><script>
parent.postMessage({type:'test:next'}, '*');
setTimeout(() => location.assign('http://127.0.0.1:${server.address().port}/canary'), 100);
</script></body></html>`);
const useService = process.argv.includes('--service');
let service, serviceRoot;
let origin = `http://127.0.0.1:${server.address().port}`;
if (useService) {
  serviceRoot = await mkdtemp(path.join(tmpdir(), 'hsr-preview-service-'));
  await writeFile(path.join(serviceRoot,'state.txt'),'approved');
  for (const name of ['index.html','shell.js','frame.js','shell.css']) await copyFile(path.join('preview_shell',name), path.join(serviceRoot,name));
  const dest = path.join(serviceRoot,'previews','37');
  await mkdir(path.join(dest,'objects'),{recursive:true});
  await writeFile(path.join(dest,'manifest.json'),JSON.stringify(manifest));
  for (const [hash, value] of objects) await writeFile(path.join(dest,'objects',hash+'.json'),value);
  const code = `import sys,time
from pathlib import Path
import tools.preview_review_server as service_module
from tools.preview_review_server import ReviewServer
service_module.CACHE_SECONDS=0
head='a'*40
def state(): return (Path(sys.argv[1])/'state.txt').read_text()
def review(repo,number):
 if state() == 'unavailable': raise RuntimeError('service unavailable')
 approved=state() == 'approved'
 return {'pr':number,'head':head,'approved':approved,'reviews':[[1,'APPROVED' if approved else 'DISMISSED','Reviewer','User',head,'2026-01-01T00:00:00Z']],'captured_at':int(time.time())}
s=ReviewServer(('127.0.0.1',0),'ganmayou2333/hsr-knowledge-base',reader=review,site=sys.argv[1],validator=lambda repo,number,manifest: state() != 'stale')
print(s.server_port,flush=True)
s.serve_forever()`;
  service = spawn('python',['-u','-c',code,serviceRoot],{stdio:['ignore','pipe','pipe']});
  const port = await new Promise((resolve,reject) => {
    let line = '', errors = '';
    service.stdout.on('data', chunk => { line += chunk; if (line.includes('\n')) resolve(Number(line.split('\n')[0])); });
    service.stderr.on('data', chunk => {errors += chunk;});
    service.on('exit', code => reject(new Error(`Preview service failed (${code}): ${errors}`)));
    service.on('error',reject);
    setTimeout(() => reject(new Error('Preview service startup timed out')),15000).unref();
  });
  if (!port) throw new Error('Invalid preview service port');
  origin = `http://127.0.0.1:${port}`;
}
const profile = await mkdtemp(path.join(tmpdir(), 'hsr-preview-browser-'));
const browser = spawn(browserPath, ['--headless=new','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'], {stdio:'ignore'});
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
let socket;
try {
  let port;
  for (let i=0;i<300;i++) {
    try { port = Number((await readFile(path.join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0]); break; }
    catch { if (browser.exitCode !== null) throw new Error('Browser exited'); await wait(100); }
  }
  if (!port) throw new Error('Browser did not start');
  const info = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
  socket = new WebSocket(info.webSocketDebuggerUrl);
  await new Promise((resolve,reject) => {socket.onopen=resolve; socket.onerror=reject;});
  let id=0; const pending=new Map();
  function send(method, params={}, sessionId) {
    const command = {id:++id,method,params,...(sessionId ? {sessionId} : {})};
    return new Promise((resolve,reject) => {pending.set(command.id,{resolve,reject}); socket.send(JSON.stringify(command)); setTimeout(() => {if(pending.delete(command.id)) reject(new Error('CDP timeout: '+method));},15000).unref();});
  }
  let githubRequests = 0;
  socket.onmessage = event => {
    const m=JSON.parse(event.data);
    if (m.id && pending.has(m.id)) { const p=pending.get(m.id); pending.delete(m.id); m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result); }
    if (m.method === 'Fetch.requestPaused') {
      githubRequests++;
      const data = m.params.request.url.includes('/reviews?') ? [{...review,state:reviewState}]
        : m.params.request.url.includes('/actions/runs/')
        ? {event:'pull_request', conclusion:'success',head_sha:head,path:'.github/workflows/wiki-quality.yml',pull_requests:[{number:37,head:{sha:head},base:{sha:base}}]}
        : {state:'open',head:{sha:stale ? 'c'.repeat(40) : head},base:{sha:base}};
      const delay = !m.params.request.url.includes('/actions/runs/') && delayNextPr ? 800 : 0;
      if (delay) delayNextPr = false;
      setTimeout(() => send('Fetch.fulfillRequest',{requestId:m.params.requestId,responseCode:200,responseHeaders:[{name:'Content-Type',value:'application/json'},{name:'Access-Control-Allow-Origin',value:'*'}],body:Buffer.from(JSON.stringify(data)).toString('base64')},m.sessionId).catch(() => {}), delay);
    }
  };
  const target=await send('Target.createTarget',{url:'about:blank'});
  const {sessionId}=await send('Target.attachToTarget',{targetId:target.targetId,flatten:true});
  const cdp=(method,params={})=>send(method,params,sessionId);
  await cdp('Page.enable'); await cdp('Runtime.enable');
  await cdp('Page.addScriptToEvaluateOnNewDocument', {source: `if (window === top) { window.testEvents = []; addEventListener('securitypolicyviolation', e => window.testEvents.push({type:'test:csp', blocked:e.blockedURI, directive:e.violatedDirective})); addEventListener('message', e => { if (e.data?.type?.startsWith('test:')) window.testEvents.push(e.data); }); }`});
  await cdp('Fetch.enable',{patterns:[{urlPattern:'https://api.github.com/*'}]});
  const evaluate=async (expression, contextId) => {
    const reply=await cdp('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true,...(contextId ? {contextId} : {})});
    if(reply.exceptionDetails) throw new Error(JSON.stringify(reply.exceptionDetails));
    return reply.result.value;
  };
  await cdp('Page.navigate',{url:`${origin}/?pr=37`});
  let events = [];
  for (let i=0;i<100;i++) {
    await wait(100);
    try { events = await evaluate('window.testEvents || []'); if (events.some(e => e.type === 'test:next')) break; } catch {}
  }
  const outer=await evaluate("({review:document.querySelector('#review-label').textContent,notice:document.querySelector('.copyright').textContent,visible:!document.querySelector('#preview').hidden,status:document.querySelector('#status').textContent})");
  if (!events.some(e => e.type === 'test:home' && e.isolated) || !events.some(e => e.type === 'test:next')) throw new Error('Sandbox rendering or navigation failed: ' + JSON.stringify({events,outer}));
  await wait(1500);
  events = await evaluate('window.testEvents || []');
  const canaryOrigin = `http://127.0.0.1:${server.address().port}`;
  if (!events.some(e => e.type === 'test:csp' && e.directive === 'frame-src' && (e.blocked === canaryOrigin || e.blocked === canaryOrigin + '/canary'))) throw new Error('No blocked frame navigation was observed');
  if (!outer.notice.includes('非官方') || !outer.visible || canaries) throw new Error('Preview escaped its notice or made a network request');
  let approved = false;
  for (let i=0;i<100;i++) {
    await wait(100);
    if (await evaluate("document.querySelector('#review-label').textContent === '已有有效人工批准'")) { approved = true; break; }
  }
  if (!approved) throw new Error('Current authorized review was not shown');
  reviewState = 'DISMISSED';
  if (useService) await writeFile(path.join(serviceRoot,'state.txt'),'revoked');
  await evaluate("document.dispatchEvent(new Event('visibilitychange'))");
  let unreviewed = false;
  for (let i=0;i<100;i++) {
    await wait(100);
    if (await evaluate("document.querySelector('#review-label').textContent === '未经人工审查'")) { unreviewed = true; break; }
  }
  await wait(500);
  if (!unreviewed || !await evaluate("document.querySelector('#review-label').textContent === '未经人工审查'")) throw new Error('Dismissed approval remained visible');
  if (!useService) {
    reviewState = 'APPROVED';
    captured = Math.floor(Date.now() / 1000) - 61;
    await evaluate("document.dispatchEvent(new Event('visibilitychange'))");
    await wait(500);
    if (!await evaluate("document.querySelector('#review-label').textContent === '未经人工审查'")) throw new Error('Expired approval remained visible');
  }
  delayNextPr = true;
  await evaluate("document.dispatchEvent(new Event('visibilitychange'))");
  await wait(100);
  stale = true;
  if (useService) await writeFile(path.join(serviceRoot,'state.txt'),'stale');
  await evaluate("document.dispatchEvent(new Event('visibilitychange'))");
  let hidden = false;
  for (let i=0;i<100;i++) {
    await wait(100);
    if (await evaluate("document.querySelector('#preview').hidden")) { hidden = true; break; }
  }
  if (!hidden) throw new Error('Stale preview remained visible');
  await wait(1000);
  if (!await evaluate("document.querySelector('#preview').hidden")) throw new Error('An older verification restored a stale preview');
  if (useService) {
    await writeFile(path.join(serviceRoot,'state.txt'),'approved');
    await evaluate("document.dispatchEvent(new Event('visibilitychange'))");
    let restored = false;
    for (let i=0;i<100;i++) {
      await wait(100);
      if (await evaluate("!document.querySelector('#preview').hidden")) { restored = true; break; }
    }
    if (!restored) throw new Error('Valid preview did not recover');
    await writeFile(path.join(serviceRoot,'state.txt'),'unavailable');
    await evaluate("document.dispatchEvent(new Event('visibilitychange'))");
    await wait(500);
    if (!await evaluate("document.querySelector('#preview').hidden")) throw new Error('Unavailable service left preview visible');
  }
  if (useService && githubRequests) throw new Error('Browser contacted GitHub in service mode');
  console.log('PASS: sandbox rendering, parent isolation, blocked network and frame navigation, notice integrity, navigation and stale preview');
} finally {
  socket?.close(); browser.kill(); service?.kill(); server.close(); await wait(250);
  if (serviceRoot) await rm(serviceRoot,{recursive:true,force:true,maxRetries:5,retryDelay:200});
  await rm(profile,{recursive:true,force:true,maxRetries:5,retryDelay:200});
}
