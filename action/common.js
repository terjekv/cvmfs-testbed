'use strict';
const fs = require('node:fs');
const crypto = require('node:crypto');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const root = path.resolve(__dirname, '..');

function write(file, key, value) {
  if (!file) throw new Error('GitHub Actions environment file is missing');
  const delimiter = `cvmfs_testbed_${crypto.randomUUID()}`;
  fs.appendFileSync(file, `${key}<<${delimiter}\n${value}\n${delimiter}\n`);
}

function invoke(state, args, capture = false) {
  const result = spawnSync('python3', [path.join(root, 'testbed.py'), '--state-dir', state, ...args], {
    encoding: 'utf8', stdio: capture ? ['ignore', 'pipe', 'inherit'] : 'inherit',
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`cvmfs-testbed ${args[0]} failed (exit ${result.status})`);
  return result.stdout;
}

module.exports = { root, write, invoke };
