const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("cutvokeDesktop", Object.freeze({
  initialLanguage: ipcRenderer.sendSync("cutvoke:language-state"),
  getLanguageState: () => ipcRenderer.sendSync("cutvoke:language-state"),
  setLanguagePreference: value => ipcRenderer.invoke("cutvoke:language-set", value),
  chooseCacheDirectory: () => ipcRenderer.invoke("cutvoke:choose-cache"),
  revealCacheDirectory: () => ipcRenderer.invoke("cutvoke:reveal-cache"),
  info: () => ipcRenderer.invoke("cutvoke:info"),
}));
