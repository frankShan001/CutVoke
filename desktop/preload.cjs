const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("cutvokeDesktop", Object.freeze({
  chooseCacheDirectory: () => ipcRenderer.invoke("cutvoke:choose-cache"),
  revealCacheDirectory: () => ipcRenderer.invoke("cutvoke:reveal-cache"),
  info: () => ipcRenderer.invoke("cutvoke:info"),
}));
