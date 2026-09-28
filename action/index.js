'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { root, write, invoke } = require('./common');

try {
  if (process.platform !== 'linux') throw new Error('This action requires a Linux runner with Docker or rootful Podman and mount privileges');
  const input = name => process.env[`INPUT_${name.toUpperCase()}`] || '';
  const cleanup = input('cleanup') || 'true';
  if (!['true', 'false'].includes(cleanup)) throw new Error('cleanup must be true or false');
  const sudo = input('sudo') || 'false';
  if (!['true', 'false'].includes(sudo)) throw new Error('sudo must be true or false');
  const state = input('state-directory')
    ? path.resolve(input('state-directory'))
    : fs.mkdtempSync(path.join(process.env.RUNNER_TEMP, 'cvmfs-testbed-'));
  // Record ownership before setup so a partially failed deployment is cleaned up too.
  if (fs.existsSync(path.join(state, 'state.json'))) throw new Error(`State directory already owns a deployment: ${state}`);
  write(process.env.GITHUB_STATE, 'testbed_state', state);
  write(process.env.GITHUB_STATE, 'testbed_cleanup', cleanup);
  const args = ['up', '--cvmfs-version', input('cvmfs-version') || '2.14.1', '--platform', input('platform') || 'auto'];
  args.push('--runtime', input('runtime') || 'auto');
  if (sudo === 'true') args.push('--sudo');
  if (input('project-name')) args.push('--project-name', input('project-name'));
  invoke(state, args);
  const endpoints = JSON.parse(fs.readFileSync(path.join(state, 'endpoints.json'), 'utf8'));
  const outputs = {
    'state-directory': state, 'endpoints-file': path.join(state, 'endpoints.json'),
    'keys-directory': endpoints.keys_directory, network: endpoints.network,
    's0-url': endpoints.host.s0, 's1-url': endpoints.host.s1,
    's1-s3-url': endpoints.host['s1-s3'], 'proxy-url': endpoints.host.squid,
  };
  for (const [key, value] of Object.entries(outputs)) write(process.env.GITHUB_OUTPUT, key, value);
  write(process.env.GITHUB_ENV, 'CVMFS_TESTBED_STATE', state);
  write(process.env.GITHUB_ENV, 'CVMFS_TESTBED_ENDPOINTS', outputs['endpoints-file']);
  fs.appendFileSync(process.env.GITHUB_PATH, path.join(root, 'bin') + '\n');
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
