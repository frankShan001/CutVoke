// QA-only access to the live child handle. Never signal an arbitrary numeric PID.
const { randomUUID } = require('node:crypto');
const READY = 'CUTVOKE_QA_STACK_READY=';
function createStackCapture(child) {
  const token = randomUUID();
  let ready = false, exited = false, buffer = '';
  const onData = chunk => {
    buffer += chunk.toString('utf8');
    let newline;
    while ((newline = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line === READY + child.pid) ready = true;
    }
    // Media diagnostics can be large; only the short readiness line matters.
    if (buffer.length > 4096) buffer = '';
  };
  child.stderr.on('data', onData);
  const close = () => { exited = true; ready = false; child.stderr.off('data', onData); };
  child.once('exit', close);
  child.once('error', close);
  const live = () => ready && !exited && child.exitCode === null && child.signalCode === null;
  return {
    identity: () => live() ? { pid: child.pid, token } : null,
    capture(identity) {
      if (!live() || identity?.pid !== child.pid || identity?.token !== token) return false;
      // ChildProcess.kill uses the owned process handle and refuses a closed
      // handle. A stale PID, restarted engine or missing handler is never enough.
      try { return child.kill('SIGUSR2'); } catch { return false; }
    },
  };
}
function scheduleStackCapture(capture, report, delay = 60_000) {
  let active = true;
  const timer = setTimeout(() => {
    if (!active) return;
    Promise.resolve().then(() => active ? capture() : false)
      .then(captured => { if (active) report({ captured }); })
      .catch(error => { if (active) report({ error: error.message }); });
  }, delay);
  return () => { active = false; clearTimeout(timer); };
}
module.exports = { createStackCapture, scheduleStackCapture };
