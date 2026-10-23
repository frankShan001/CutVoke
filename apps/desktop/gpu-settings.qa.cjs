const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
module.exports = async ({window, app}) => {
  const profile = process.env.CUTVOKE_DESKTOP_QA_HOME;
  const scenario = process.env.CUTVOKE_DESKTOP_QA_CASE;
  const until = async expression => {
    for (let i = 0; i < 400; i++) {
      if (await window.webContents.executeJavaScript(expression)) return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error('Timed out: ' + expression);
  };
  await until("!!document.querySelector('.cache-settings-trigger')");
  const initial = await window.webContents.executeJavaScript('window.cutvokeDesktop.settings()');
  assert.equal(initial.gpuAcceleration, scenario !== 'enable');
  const info = await window.webContents.executeJavaScript('window.cutvokeDesktop.info()');
  assert.equal(info.mcp.mcpServers.cutvoke.env.CUTVOKE_GPU_ACCELERATION, initial.gpuAcceleration ? '1' : '0');
  const features = app.getGPUFeatureStatus();
  if (!initial.gpuAcceleration) assert.notEqual(features.gpu_compositing, 'enabled');
  if (initial.setupComplete) await window.webContents.executeJavaScript("document.querySelector('.cache-settings-trigger').click()");
  await until("!!document.querySelector('.settings-gpu-toggle input')");
  assert.equal(await window.webContents.executeJavaScript("document.querySelector('.settings-gpu-toggle input').checked"), initial.gpuAcceleration);
  fs.writeFileSync(path.join(profile, scenario + '.png'), (await window.webContents.capturePage()).toPNG());
  const result = {passed:true, initial, features};
  if (scenario !== 'verify') {
    app.relaunch = () => { result.relaunchRequested = true; };
    await window.webContents.executeJavaScript("document.querySelector('.settings-gpu-toggle input').click()");
    const changed = !initial.gpuAcceleration;
    const saved = await window.webContents.executeJavaScript(`(async () => {
      const settings=await window.cutvokeDesktop.settings();
      const cache=await (await fetch('/api/v1/preview-cache')).json();
      return window.cutvokeDesktop.saveSettings({projectDirectory:settings.projectDirectory,cacheDirectory:cache.directory,maxBytes:cache.maxBytes,gpuAcceleration:${changed}});
    })()`);
    assert.equal(saved.restartRequired, true);
    assert.equal(result.relaunchRequested, true);
    assert.equal(JSON.parse(fs.readFileSync(path.join(profile,'settings.json'),'utf8')).gpuAcceleration, changed);
  }
  fs.writeFileSync(path.join(profile, scenario + '-result.json'), JSON.stringify(result,null,2));
};
