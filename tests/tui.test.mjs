import assert from 'node:assert/strict';
import test from 'node:test';

import {
  RosettaExperience,
  phase,
  pulseFrames,
} from '../.opencode/plugins/rosetta-experience.js';

const wait = (milliseconds) => new Promise(resolve => setTimeout(resolve, milliseconds));

test('execution tools receive an animated proof phase', () => {
  const verify = phase('rosetta_verify_change');
  assert.equal(verify.label, 'PROVE');
  assert.equal(verify.pulse, true);
  assert.match(verify.message, /output \+ globals/);

  const execute = phase('execute-routine');
  assert.equal(execute.label, 'EXECUTE');
  assert.equal(execute.pulse, true);
  assert.equal(pulseFrames.length, 8);
  assert.equal(new Set(pulseFrames).size, 5);
  assert.equal(phase('read_routine'), null);
});

test('the proof pulse moves while a tool runs and stops on its verdict', async () => {
  const toasts = [];
  const hooks = await RosettaExperience({
    client: {
      tui: {
        async showToast({ body }) {
          toasts.push(body);
        },
      },
    },
  });

  await hooks['tool.execute.before']({
    tool: 'verify_change',
    sessionID: 'session',
    callID: 'proof-call',
  });
  await wait(930);

  assert.ok(toasts.length >= 2, 'the running indicator never advanced');
  assert.notEqual(toasts[0].title, toasts[1].title);
  assert.match(toasts[0].title, /Rosetta · PROVE/);

  await hooks['tool.execute.after'](
    { tool: 'verify_change', sessionID: 'session', callID: 'proof-call', args: {} },
    { title: 'Verification', output: 'NOT EQUIVALENT', metadata: {} },
  );
  const stoppedAt = toasts.length;
  assert.match(toasts.at(-1).title, /DIVERGENCE CAUGHT/);

  await wait(930);
  assert.equal(toasts.length, stoppedAt, 'the proof pulse continued after the verdict');
});
