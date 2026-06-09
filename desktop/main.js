const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const http = require('http');
const { initAutoUpdater, getUpdateStatus, restartToUpdate } = require('./updater');

const BACKEND_PORT = process.env.SENTINEL_EARN_PORT || '5120';
const API = `http://127.0.0.1:${BACKEND_PORT}`;
let backendProc = null;
let mainWindow = null;

function getBackendRoot() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'backend');
  }
  return path.join(__dirname, '..', 'backend');
}

function findPython() {
  const backendRoot = getBackendRoot();
  const candidates = [
    path.join(backendRoot, '.venv', 'Scripts', 'python.exe'),
    path.join(backendRoot, 'venv', 'Scripts', 'python.exe'),
    'python',
  ];
  for (const c of candidates) {
    try {
      if (c === 'python' || require('fs').existsSync(c)) return c;
    } catch (_) {}
  }
  return 'python';
}

function pingBackend() {
  return new Promise((resolve) => {
    const req = http.get(`${API}/api/ping`, (res) => {
      resolve(res.statusCode === 200);
    });
    req.on('error', () => resolve(false));
    req.setTimeout(2000, () => { req.destroy(); resolve(false); });
  });
}

async function waitForBackend(maxAttempts = 60) {
  for (let i = 0; i < maxAttempts; i++) {
    if (await pingBackend()) return true;
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

function startBackend() {
  const python = findPython();
  const backendRoot = getBackendRoot();
  const script = path.join(backendRoot, 'app.py');
  backendProc = spawn(python, [script], {
    cwd: backendRoot,
    env: { ...process.env, SENTINEL_EARN_PORT: BACKEND_PORT },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  backendProc.stdout.on('data', (d) => console.log('[backend]', d.toString()));
  backendProc.stderr.on('data', (d) => console.error('[backend]', d.toString()));
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 960,
    minHeight: 640,
    backgroundColor: '#0a0e14',
    title: 'Sentinel Earn',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.loadFile('index.html');
  mainWindow.on('closed', () => { mainWindow = null; });
}

app.whenReady().then(async () => {
  startBackend();
  const ok = await waitForBackend();
  if (!ok) console.warn('Backend did not respond in time — UI may retry');
  createWindow();
  initAutoUpdater(() => mainWindow, {
    isDev: !app.isPackaged || process.env.NODE_ENV === 'development',
  });
});

app.on('window-all-closed', () => {
  if (backendProc) backendProc.kill();
  if (process.platform !== 'darwin') app.quit();
});

ipcMain.handle('get-api-base', () => API);
ipcMain.handle('earn:getUpdateStatus', () => getUpdateStatus());
ipcMain.handle('earn:restartToUpdate', () => restartToUpdate());
