'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { invoke } = require('./common');

const state = process.env.STATE_testbed_state;
if (state && fs.existsSync(path.join(state, 'state.json'))) {
  try {
    invoke(state, ['logs', '--output', path.join(state, 'post-job-logs')]);
  } catch (error) {
    console.error(`Could not collect post-job logs: ${error.message}`);
  } finally {
    if (process.env.STATE_testbed_cleanup === 'true') {
      try { invoke(state, ['down']); }
      catch (error) { console.error(error.message); process.exitCode = 1; }
    }
  }
}
