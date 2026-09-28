'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const common = require('../action/common');

test('environment file values cannot inject a second output', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'testbed-action-'));
  try {
    const file = path.join(dir, 'output');
    common.write(file, 'value', 'line one\nother=not-an-output');
    const text = fs.readFileSync(file, 'utf8');
    const delimiter = text.split('\n')[0].split('<<')[1];
    assert.equal(text, `value<<${delimiter}\nline one\nother=not-an-output\n${delimiter}\n`);
  } finally { fs.rmSync(dir, { recursive: true }); }
});

function postFixture(fn) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'testbed-action-'));
  const previousInvoke = common.invoke;
  const previousEnv = { ...process.env };
  const previousExit = process.exitCode;
  const calls = [];
  fs.writeFileSync(path.join(dir, 'state.json'), '{}');
  process.env.STATE_testbed_state = dir;
  process.env.STATE_testbed_cleanup = 'true';
  const script = require.resolve('../action/post');
  try {
    fn({ dir, calls, script });
  } finally {
    common.invoke = previousInvoke;
    process.env = previousEnv;
    process.exitCode = previousExit;
    delete require.cache[script];
    fs.rmSync(dir, { recursive: true });
  }
}

test('a failed log collection still cleans up a partially started deployment', () => {
  postFixture(({ calls, script }) => {
    common.invoke = (state, args) => {
      calls.push(args[0]);
      if (args[0] === 'logs') throw new Error('service unavailable');
    };
    require(script);
    assert.deepEqual(calls, ['logs', 'down']);
  });
});

test('cleanup false preserves the deployment', () => {
  postFixture(({ calls, script }) => {
    process.env.STATE_testbed_cleanup = 'false';
    common.invoke = (state, args) => calls.push(args[0]);
    require(script);
    assert.deepEqual(calls, ['logs']);
  });
});

test('cleanup failure is not silently ignored', () => {
  postFixture(({ script }) => {
    common.invoke = (state, args) => { if (args[0] === 'down') throw new Error('Docker unavailable'); };
    require(script);
    assert.equal(process.exitCode, 1);
  });
});
