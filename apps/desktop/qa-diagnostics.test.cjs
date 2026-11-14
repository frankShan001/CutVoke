const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { createStackCapture } = require('./qa-diagnostics.cjs');
function engine(pid = 123) {
  const child = new EventEmitter();
  Object.assign(child, { pid, stderr: new EventEmitter(), exitCode: null, signalCode: null, killed: false, signals: [] });
  child.kill = signal => { child.signals.push(signal); child.killed = true; return true; };
  return child;
}
test('handler readiness is required, including split lines and wrong PID', () => {
  const child = engine(), capture = createStackCapture(child);
  assert.equal(capture.identity(), null);
  assert.equal(capture.capture({ pid: child.pid }), false);
  child.stderr.emit('data', Buffer.from('CUTVOKE_QA_STACK_READY=999\n'));
  assert.equal(capture.identity(), null);
  child.stderr.emit('data', Buffer.from('CUTVOKE_QA_STACK_RE'));
  child.stderr.emit('data', Buffer.from('ADY=123\n'));
  const identity = capture.identity();
  assert.equal(identity.pid, 123);
  assert.equal(capture.capture({ ...identity, pid: 999 }), false);
  assert.equal(capture.capture({ ...identity, token: 'wrong' }), false);
  assert.equal(capture.capture(identity), true);
  assert.equal(capture.capture(identity), true); // SIGUSR2 is non-terminating.
  assert.deepEqual(child.signals, ['SIGUSR2', 'SIGUSR2']);
});
test('exit, signal exit and spawn error disable capture and remove listener', () => {
  for (const event of ['exit', 'error']) {
    const child = engine(), capture = createStackCapture(child);
    child.stderr.emit('data', Buffer.from('CUTVOKE_QA_STACK_READY=123\n'));
    const identity = capture.identity();
    child.emit(event, event === 'error' ? new Error('spawn failed') : 0);
    assert.equal(capture.identity(), null);
    assert.equal(capture.capture(identity), false);
    assert.equal(child.stderr.listenerCount('data'), 0);
    assert.deepEqual(child.signals, []);
  }
  const child = engine(), capture = createStackCapture(child);
  child.stderr.emit('data', Buffer.from('CUTVOKE_QA_STACK_READY=123\n'));
  const identity = capture.identity();
  child.signalCode = 'SIGTERM';
  assert.equal(capture.capture(identity), false);
});
test('reused PID is insufficient and closed handle errors are harmless', () => {
  const old = engine(), current = engine();
  const oldCapture = createStackCapture(old), currentCapture = createStackCapture(current);
  for (const child of [old, current]) child.stderr.emit('data', Buffer.from('CUTVOKE_QA_STACK_READY=123\n'));
  const previousIdentity = oldCapture.identity();
  old.emit('exit', 0);
  assert.equal(currentCapture.capture(previousIdentity), false);
  current.kill = () => { throw new Error('closed process handle'); };
  assert.equal(currentCapture.capture(currentCapture.identity()), false);
});
test('unbounded unrelated stderr cannot fake handler readiness', () => {
  const child = engine(), capture = createStackCapture(child);
  child.stderr.emit('data', Buffer.from('x'.repeat(10000)));
  child.stderr.emit('data', Buffer.from('\nnoise CUTVOKE_QA_STACK_READY=123\n'));
  assert.equal(capture.identity(), null);
  assert.deepEqual(child.signals, []);
});
const { scheduleStackCapture } = require('./qa-diagnostics.cjs');
test('one capture at 60s, no repeated timer', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  let captures = 0;
  const results = [];
  const cancel = scheduleStackCapture(() => { captures++; return true; }, result => results.push(result));
  context.mock.timers.tick(59_999);
  await Promise.resolve();
  assert.equal(captures, 0);
  context.mock.timers.tick(1);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(captures, 1);
  assert.deepEqual(results, [{ captured: true }]);
  context.mock.timers.tick(120_000);
  await Promise.resolve();
  assert.equal(captures, 1);
  cancel();
});
test('finally cancellation prevents pending capture and failures stay diagnostic', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  let captures = 0;
  const cancel = scheduleStackCapture(() => { captures++; }, () => {});
  cancel();
  context.mock.timers.tick(60_000);
  await Promise.resolve();
  assert.equal(captures, 0);
  const results = [];
  const stop = scheduleStackCapture(() => { throw new Error('gone'); }, result => results.push(result));
  context.mock.timers.tick(60_000);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(results, [{ error: 'gone' }]);
  stop();
});
test('real owned child handles repeated diagnostic signals without exiting', { skip: process.platform === 'win32', timeout: 5000 }, async context => {
  const { spawn } = require('node:child_process');
  const { once } = require('node:events');
  const child = spawn(process.execPath, ['-e', `
    process.on('SIGUSR2', () => process.stdout.write('captured\\n'));
    process.stderr.write('CUTVOKE_QA_STACK_READY=' + process.pid + '\\n');
    setInterval(() => {}, 1000);
  `], { stdio: ['ignore', 'pipe', 'pipe'] });
  context.after(async () => {
    if (child.exitCode === null && child.signalCode === null) {
      const exited = once(child, 'exit'); child.kill('SIGTERM'); await exited;
    }
  });
  const capture = createStackCapture(child);
  await once(child.stderr, 'data');
  const identity = capture.identity();
  assert.equal(identity.pid, child.pid);
  for (let index = 0; index < 2; index++) {
    const output = once(child.stdout, 'data');
    assert.equal(capture.capture(identity), true);
    assert.equal(String((await output)[0]), 'captured\n');
    assert.equal(child.exitCode, null);
  }
  const exited = once(child, 'exit'); child.kill('SIGTERM'); await exited;
  assert.equal(capture.capture(identity), false);
});
test('packaging includes the QA helper without shipping test files', () => {
  const files = require('./package.json').build.files;
  assert.ok(files.includes('qa-diagnostics.cjs'));
  assert.ok(!files.includes('qa-diagnostics.test.cjs'));
});
