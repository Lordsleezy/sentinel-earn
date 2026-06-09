const { autoUpdater } = require('electron-updater');

let status = { state: 'idle' };
let getMainWindow = () => null;

function sendStatus() {
  const win = getMainWindow();
  if (win && !win.isDestroyed()) {
    win.webContents.send('earn:update', status);
  }
}

function getUpdateStatus() {
  return status;
}

function restartToUpdate() {
  autoUpdater.quitAndInstall(false, true);
}

function initAutoUpdater(windowGetter, { isDev }) {
  getMainWindow = windowGetter;

  if (isDev) {
    console.log('[updater] disabled in development');
    return;
  }

  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.allowDowngrade = false;

  autoUpdater.on('error', (error) => {
    console.warn('[updater] error:', error.message);
    status = { state: 'error' };
    sendStatus();
  });

  autoUpdater.on('checking-for-update', () => {
    status = { state: 'checking' };
    sendStatus();
  });

  autoUpdater.on('update-available', (info) => {
    console.log('[updater] update available:', info.version);
    status = { state: 'downloading', percent: 0 };
    sendStatus();
  });

  autoUpdater.on('update-not-available', () => {
    status = { state: 'idle' };
    sendStatus();
  });

  autoUpdater.on('download-progress', (progress) => {
    status = { state: 'downloading', percent: progress.percent };
    sendStatus();
  });

  autoUpdater.on('update-downloaded', (info) => {
    console.log('[updater] downloaded:', info.version);
    status = { state: 'ready', version: info.version };
    sendStatus();
  });

  setTimeout(() => {
    console.log('[updater] checking for updates…');
    autoUpdater.checkForUpdates().catch((err) => {
      console.warn('[updater] check failed:', err);
    });
  }, 5000);
}

module.exports = { initAutoUpdater, getUpdateStatus, restartToUpdate };
