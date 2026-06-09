const { app, BrowserWindow, ipcMain, shell } = require('electron');
const path = require('path');
const { spawn, spawnSync } = require('child_process');
const http = require('http');
const { initAutoUpdater, getUpdateStatus, restartToUpdate } = require('./updater');

let backendPort = parseInt(process.env.SENTINEL_EARN_PORT || '5120', 10);
let API = `http://127.0.0.1:${backendPort}`;
let backendProc = null;
let mainWindow = null;
let backendStatus = { state: 'idle' };

function getBackendRoot() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'backend');
  }
  return path.join(__dirname, '..', 'backend');
}

function emitBackendStatus() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('earn:backend-status', backendStatus);
  }
}

function verifyPython(exe) {
  try {
    const r = spawnSync(exe, ['--version'], { timeout: 8000, encoding: 'utf8' });
    if (r.status !== 0) return false;
    const m = (r.stdout || r.stderr || '').match(/Python (\d+)\.(\d+)/);
    if (!m) return false;
    const major = parseInt(m[1], 10);
    const minor = parseInt(m[2], 10);
    return major > 3 || (major === 3 && minor >= 10);
  } catch {
    return false;
  }
}

function resolvePython() {
  const backendRoot = getBackendRoot();
  const candidates = [
    path.join(backendRoot, '.venv', 'Scripts', 'python.exe'),
    path.join(backendRoot, 'venv', 'Scripts', 'python.exe'),
    'python',
    'python3',
    'py',
  ];
  for (const c of candidates) {
    if (c.includes(path.sep) && !require('fs').existsSync(c)) continue;
    if (verifyPython(c)) return { ok: true, path: c };
  }
  return { ok: false, error: 'python_missing' };
}

async function pickBackendPort(start = 5120) {
  for (let port = start; port < start + 20; port++) {
    const ok = await new Promise((resolve) => {
      const srv = http.createServer();
      srv.once('error', () => resolve(false));
      srv.once('listening', () => {
        srv.close();
        resolve(true);
      });
      srv.listen(port, '127.0.0.1');
    });
    if (ok) return port;
  }
  return start;
}

function pingBackend(port = backendPort) {
  return new Promise((resolve) => {
    const req = http.get(`http://127.0.0.1:${port}/api/ping`, (res) => {
      resolve(res.statusCode === 200);
    });
    req.on('error', () => resolve(false));
    req.setTimeout(2000, () => {
      req.destroy();
      resolve(false);
    });
  });
}

async function waitForBackend(maxAttempts = 120) {
  for (let i = 0; i < maxAttempts; i++) {
    if (await pingBackend()) return true;
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

function killBackend() {
  if (!backendProc || backendProc.killed) return;
  try {
    if (process.platform === 'win32' && backendProc.pid) {
      spawnSync('taskkill', ['/pid', String(backendProc.pid), '/T', '/F'], { stdio: 'ignore' });
    } else {
      backendProc.kill('SIGTERM');
    }
  } catch (_) {}
  backendProc = null;
}

function startBackend() {
  const py = resolvePython();
  if (!py.ok) {
    backendStatus = { state: 'error', code: 'python_missing' };
    emitBackendStatus();
    return false;
  }

  const backendRoot = getBackendRoot();
  const script = path.join(backendRoot, 'app.py');
  backendStatus = { state: 'starting', port: backendPort };
  emitBackendStatus();

  backendProc = spawn(py.path, [script], {
    cwd: backendRoot,
    env: { ...process.env, SENTINEL_EARN_PORT: String(backendPort) },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });

  backendProc.on('error', (err) => {
    backendStatus = { state: 'error', code: 'spawn_failed', message: err.message };
    emitBackendStatus();
  });
  backendProc.stdout.on('data', (d) => console.log('[backend]', d.toString()));
  backendProc.stderr.on('data', (d) => console.error('[backend]', d.toString()));
  backendProc.on('exit', (code) => {
    if (backendStatus.state === 'ready') return;
    backendStatus = { state: 'error', code: 'exited', message: `Backend exited (${code})` };
    emitBackendStatus();
  });
  return true;
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
  mainWindow.webContents.once('did-finish-load', () => emitBackendStatus());
  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  createWindow();
  backendPort = await pickBackendPort(backendPort);
  API = `http://127.0.0.1:${backendPort}`;

  if (startBackend()) {
    const ok = await waitForBackend(120);
    backendStatus = ok
      ? { state: 'ready', port: backendPort, api: API }
      : { state: 'error', code: 'timeout', message: 'Backend did not start in time' };
    emitBackendStatus();
  }

  initAutoUpdater(() => mainWindow, {
    isDev: !app.isPackaged || process.env.NODE_ENV === 'development',
  });
});

app.on('before-quit', () => killBackend());

app.on('window-all-closed', () => {
  killBackend();
  if (process.platform !== 'darwin') app.quit();
});

ipcMain.handle('get-api-base', () => API);
ipcMain.handle('earn:getBackendStatus', () => backendStatus);
ipcMain.handle('earn:openPythonDownload', () => shell.openExternal('https://www.python.org/downloads/'));
ipcMain.handle('earn:getUpdateStatus', () => getUpdateStatus());
ipcMain.handle('earn:restartToUpdate', () => restartToUpdate());
