const {spawn, execFile, execFileSync} = require('child_process');
const http = require('http');
const path = require('path');
const fs = require('fs');
const electron = require('electron');

const root = path.join(__dirname, '..');
const viteEntry = path.join(root, 'node_modules', 'vite', 'bin', 'vite.js');
let vite;
let desktop;
let backendPid;
const pidFile = path.join(process.env.LOCALAPPDATA || root, 'Xerxes', `dev-backend-${process.pid}.pid`);
fs.mkdirSync(path.dirname(pidFile), {recursive: true});
let shuttingDown = false;
let exitCode = 0;

function probe(url) {
  return new Promise(resolve => {
    const req = http.get(url, response => { response.resume(); resolve(response.statusCode >= 200 && response.statusCode < 500); });
    req.setTimeout(500, () => { req.destroy(); resolve(false); });
    req.on('error', () => resolve(false));
  });
}

async function waitForVite() {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    if (await probe('http://127.0.0.1:5173/')) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error('Vite did not become ready on 127.0.0.1:5173');
}

function stopProcess(child) {
  if (child && !child.killed) child.kill();
}

function forceOwnedTree(child) {
  if (process.platform !== 'win32' || !child || !child.pid) return;
  try {
    const output = execFileSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], {encoding:'utf8',stdio:['ignore','pipe','pipe']});
    console.error(`[xerxes-supervisor] taskkill pid=${child.pid} result=${JSON.stringify(output.trim())}`);
  } catch (error) {
    console.error(`[xerxes-supervisor] taskkill pid=${child.pid} failed=${error.status || error.code} stderr=${String(error.stderr || '').trim()}`);
  }
}

function waitForChildExit(child, timeoutMs, fallback) {
  if (!child || child.exitCode !== null || child.killed) return Promise.resolve('already-exited');
  return new Promise(resolve => {
    const timer = setTimeout(() => { fallback(); resolve('timeout'); }, timeoutMs);
    child.once('exit', () => { clearTimeout(timer); resolve('exited'); });
  });
}

async function shutdown(code = exitCode) {
  if (shuttingDown) return;
  shuttingDown = true;
  exitCode = code;
  if (desktop && !desktop.killed) {
    try { desktop.send({type: 'shutdown'}); } catch {}
    await waitForChildExit(desktop, 6000, () => forceOwnedTree(desktop));
  }
  if (backendPid) forceOwnedTree({pid: backendPid});
  try { fs.unlinkSync(pidFile); } catch {}
  stopProcess(vite);
  setTimeout(() => process.exit(exitCode), 250);
}

async function main() {
  vite = spawn(process.execPath, [viteEntry, '--host', '127.0.0.1'], {cwd: root, stdio: ['ignore', 'inherit', 'inherit'], windowsHide: true});
  vite.once('error', error => { console.error(`[xerxes-supervisor] Vite failed: ${error.message}`); shutdown(1); });
  vite.once('exit', code => { if (!shuttingDown && code !== 0) shutdown(code || 1); });
  await waitForVite();
  const env = {...process.env, ELECTRON_DEV_SERVER: 'http://127.0.0.1:5173', XERXES_BACKEND_PID_FILE: pidFile};
  delete env.ELECTRON_RUN_AS_NODE;
  desktop = spawn(electron, ['.'], {cwd: root, env, stdio: ['ignore', 'inherit', 'inherit', 'ipc'], windowsHide: true});
  desktop.on('message', message => { if (message && message.type === 'backend') backendPid = message.pid; });
  for (let i = 0; i < 150 && !backendPid; i++) {
    try { backendPid = Number(fs.readFileSync(pidFile, 'utf8').trim()) || undefined; } catch {}
    if (!backendPid) await new Promise(resolve => setTimeout(resolve, 100));
  }
  desktop.once('error', error => { console.error(`[xerxes-supervisor] Electron failed: ${error.message}`); shutdown(1); });
  desktop.once('exit', (code, signal) => { if (!shuttingDown) shutdown(code ?? (signal ? 1 : 0)); });
}

if (require.main === module) {
  if (process.stdin.isTTY && process.stdin.setRawMode) {
    process.stdin.setRawMode(true);
    process.stdin.resume();
    process.stdin.on('data', data => { if (data.includes(3)) shutdown(0); });
  }
  process.once('SIGINT', () => shutdown(0));
  process.once('SIGTERM', () => shutdown(0));
  process.once('uncaughtException', error => { console.error(error); shutdown(1); });
  process.once('unhandledRejection', error => { console.error(error); shutdown(1); });
  main().catch(error => { console.error(`[xerxes-supervisor] startup failed: ${error.message}`); shutdown(1); });
}

module.exports = {waitForChildExit};
