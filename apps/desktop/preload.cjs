const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("cutvokeDesktop", Object.freeze({
  initialLanguage: ipcRenderer.sendSync("cutvoke:language-state"),
  getLanguageState: () => ipcRenderer.sendSync("cutvoke:language-state"),
  setLanguagePreference: value => ipcRenderer.invoke("cutvoke:language-set", value),
  chooseCacheDirectory: () => ipcRenderer.invoke("cutvoke:choose-cache"),
  revealCacheDirectory: () => ipcRenderer.invoke("cutvoke:reveal-cache"),
  info: () => ipcRenderer.invoke("cutvoke:info"),
  settings: () => ipcRenderer.invoke("cutvoke:settings"),
  saveSettings: value => ipcRenderer.invoke("cutvoke:settings-save", value),
  chooseProjectDirectory: () => ipcRenderer.invoke("cutvoke:choose-project-directory"),
  chooseExportFile: suggested => ipcRenderer.invoke("cutvoke:choose-export", suggested),
  revealExport: (projectId, jobId) => ipcRenderer.invoke("cutvoke:reveal-export", projectId, jobId),
}));
