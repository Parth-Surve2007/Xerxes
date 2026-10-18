const { app, BrowserWindow, dialog, session, ipcMain } = require('electron');
const { spawn, execFile } = require('child_process');
const net = require('net');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');

let backend;
let apiPort;
let backendCleanup;
const grantToken = crypto.randomBytes(32).toString('hex');
function startupLog(message) { try { fs.appendFileSync(path.join(app.getPath('userData'), 'startup.log'), `${new Date().toISOString()} ${message}\n`); } catch {} }

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}

function backendRoot() {
  if (app.isPackaged) return path.join(process.resourcesPath, 'backend');
  return path.join(__dirname, '..', 'backend');
}
function workspaceConfigPath() { return path.join(app.getPath('userData'), 'workspace.json'); }
function configuredWorkspace() { try { const value=JSON.parse(fs.readFileSync(workspaceConfigPath(),'utf8')).path; if (value && fs.existsSync(value) && fs.statSync(value).isDirectory()) return value; } catch {} return null; }

async function startBackend() {
  startupLog('starting backend');
  apiPort = await freePort();
  const root = backendRoot();
  const workspace = process.env.XERXES_WORKSPACE || configuredWorkspace() || '';
  const database = path.join(app.getPath('userData'), 'xerxes.db');
  const python = process.env.XERXES_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  const localModel = path.join(process.env.LOCALAPPDATA || '', 'Xerxes', 'models', 'qwen2.5-coder-7b-instruct-q4_k_m.gguf');
  const llamaServer = path.join(process.env.LOCALAPPDATA || '', 'Xerxes', 'runtime', 'b11712-win-cpu-x64', 'llama-server.exe');
  backend = spawn(python, ['app.py'], {
    cwd: root,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
    env: {...process.env, PORT: String(apiPort), XERXES_WORKSPACE: workspace, XERXES_DATABASE: database, XERXES_GRANT_TOKEN: grantToken,
      ...(fs.existsSync(localModel) && fs.existsSync(llamaServer) ? {XERXES_LLAMA_SERVER_EXE: llamaServer, XERXES_LOCAL_MODEL_PATH: localModel, XERXES_LOCAL_MODEL:'qwen2.5-coder-7b-instruct-q4_k_m'} : {})}
  });
  if (process.env.XERXES_BACKEND_PID_FILE) fs.writeFileSync(process.env.XERXES_BACKEND_PID_FILE, String(backend.pid), 'utf8');
  if (process.send) process.send({type:'backend', pid:backend.pid, port:apiPort});
  backend.stdout.on('data', data => console.log(`[xerxes-backend] ${data}`));
  backend.stderr.on('data', data => console.error(`[xerxes-backend] ${data}`));
  backend.on('error', error => console.error('[xerxes-backend] startup failed', error));
  backend.on('exit', (code, signal) => { if (!app.isQuitting) console.error(`[xerxes-backend] exited code=${code} signal=${signal}`); });
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    try { const response = await fetch(`http://127.0.0.1:${apiPort}/api/v1/health`); if (response.ok) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  if (backend && !backend.killed) backend.kill();
  throw new Error('Local Xerxes backend did not become ready');
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1440, height: 920, minWidth: 960, minHeight: 640,
    title: 'Xerxes',
    titleBarStyle: 'hidden',
    titleBarOverlay: {color: '#111111', symbolColor: '#a0a0a0', height: 32},
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true }
  });
  win.setAutoHideMenuBar(true);
  win.setMenuBarVisibility(false);
  win.webContents.setWindowOpenHandler(() => ({action: 'deny'}));
  win.webContents.on('will-navigate', event => event.preventDefault());
  win.webContents.on('console-message', (_event, level, message, line, source) => console.error(`[xerxes-renderer] level=${level} ${source}:${line} ${message}`));
  win.webContents.on('did-fail-load', (_event, code, description, url) => console.error(`[xerxes-renderer] load failed code=${code} description=${description} url=${url}`));
  win.webContents.on('did-fail-load', (_event, code, description, url) => startupLog(`renderer load failed code=${code} ${description} ${url}`));
  win.webContents.on('did-finish-load', () => startupLog(`renderer loaded ${win.webContents.getURL()}`));
  win.webContents.on('dom-ready', () => console.error(`[xerxes-renderer] dom-ready url=${win.webContents.getURL()}`));
  win.webContents.on('did-finish-load', () => console.error(`[xerxes-renderer] finished url=${win.webContents.getURL()}`));
  const dev = process.env.ELECTRON_DEV_SERVER;
  if (dev) win.loadURL(`${dev}?apiPort=${apiPort}`); else win.loadFile(path.join(__dirname, '..', 'dist', 'index.html'), {query:{apiPort:String(apiPort)}}).catch(error => startupLog(`renderer load exception ${error.message}`));
}

app.whenReady().then(async () => {
  ipcMain.handle('select-folder', async () => {
    const picked = await dialog.showOpenDialog({properties:['openDirectory','createDirectory']});
    if (picked.canceled || !picked.filePaths[0]) return {cancelled:true};
    const response = await fetch(`http://127.0.0.1:${apiPort}/api/v1/grants`, {method:'POST',headers:{'Content-Type':'application/json','X-Xerxes-Grant-Token':grantToken},body:JSON.stringify({path:picked.filePaths[0]})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || 'Folder grant failed');
    return {cancelled:false,grant:data};
  });
  ipcMain.handle('select-workspace', async () => {
    const picked = await dialog.showOpenDialog({properties:['openDirectory','createDirectory'],title:'Choose Xerxes workspace'});
    if (picked.canceled || !picked.filePaths[0]) return {cancelled:true};
    const response = await fetch(`http://127.0.0.1:${apiPort}/api/v1/workspace`, {method:'POST',headers:{'Content-Type':'application/json','X-Xerxes-Grant-Token':grantToken},body:JSON.stringify({path:picked.filePaths[0]})});
    const data = await response.json(); if (!response.ok) throw new Error(data.error?.message || 'Workspace selection failed');
    fs.writeFileSync(workspaceConfigPath(), JSON.stringify({path:picked.filePaths[0]}), 'utf8'); return {cancelled:false,workspace:data.workspace};
  });
  ipcMain.handle('revoke-folder', async (_event, grantId) => {
    if (typeof grantId !== 'string' || !grantId) throw new Error('invalid grant');
    const response = await fetch(`http://127.0.0.1:${apiPort}/api/v1/grants/${encodeURIComponent(grantId)}`, {method:'DELETE',headers:{'X-Xerxes-Grant-Token':grantToken}});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || 'Folder revocation failed');
    return data;
  });
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    const development = Boolean(process.env.ELECTRON_DEV_SERVER);
    const policy = development
      ? "default-src 'self' http://127.0.0.1:5173; connect-src 'self' ws://127.0.0.1:5173 http://127.0.0.1:*; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"
      : "default-src 'self'; connect-src 'self' http://127.0.0.1:* http://localhost:*; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'";
    callback({responseHeaders: {...details.responseHeaders, 'Content-Security-Policy': [policy]}});
  });
  try {
    await startBackend();
    createWindow();
  } catch (error) {
    startupLog(`startup failed ${error.stack || error.message}`);
    console.error('[xerxes] backend startup failed', error);
    await dialog.showMessageBox({type:'error', title:'Xerxes backend failed to start', message:'Xerxes could not start its owned local backend.', detail:error.message});
    app.quit();
  }
});

function stopBackend() {
  if (backendCleanup) return backendCleanup;
  backendCleanup = new Promise(resolve => {
    if (!backend || backend.exitCode !== null) return resolve();
    const timer = setTimeout(resolve, 5000);
    backend.once('exit', () => { clearTimeout(timer); resolve(); });
    if (process.platform === 'win32' && backend.pid) {
      execFile('taskkill', ['/PID', String(backend.pid), '/T', '/F'], () => {});
    } else {
      backend.kill();
    }
  });
  return backendCleanup;
}
app.on('before-quit', event => {
  if (app.isQuitting) return;
  event.preventDefault();
  app.isQuitting = true;
  stopBackend().then(() => app.quit());
});
process.on('SIGINT', () => app.quit());
process.on('SIGTERM', () => app.quit());
if (process.on) process.on('message', message => { if (message && message.type === 'shutdown') app.quit(); });
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
