const el = (id) => document.getElementById(id);
const importedGoals = new Map();
const verdicts = {
  passed: 'Apple settled in the bowl', task_timeout: 'Task timed out',
  api_error: 'Dream-1 or setup error', sim_error: 'Simulator error', canceled: 'Run canceled',
  unscored: 'Imported scene run complete',
  wrong_bay: 'Wrong destination bay', wrong_object_moved: 'Unrequested fruit moved',
};
const statuses = {starting: 'Preparing simulator', waiting: 'Waiting for Dream-1', running: 'Executing actions', stopping: 'Stopping simulator'};
const demoPrompt = 'Pick up the red apple with the arm closest to it. Grasp from above, lift it clear of the table, move it directly over the brown bowl at the center, and open the gripper to drop it into the bowl.';
const demoPromptFor = (task) => task === 'orange'
  ? 'Pick up the orange, carry it over the center of the bowl, lower it just inside the rim, then open the gripper and lift away.'
  : demoPrompt;
const defaultPrompt = (task) => task === 'relay'
  ? 'Use the left arm to put the apple on the transfer plate, then use the right arm to pick it up from the plate and place it in the bowl.'
  : `put ${task} into the bowl`;
const taskName = (run) => run?.task ?? 'apple';
function addRelayLayout(id) {
  if (/^relay-(holdout|train)-\d{2}$/.test(id)) window.addSceneOption(id, id.replaceAll('-', ' '));
}
const sortingCase = (id) => /^sort-(holdout|train)-(apple|orange)-(near|far)_bay-(\d{2})$/.exec(id);
function addSortingLayout(id) {
  const match = sortingCase(id);
  if (!match) return;
  const [, split, fruit, bay, number] = match;
  window.addSceneOption(id, `${fruit} → ${bay === 'near' ? 'blue' : 'amber'} bay · ${split} ${number}`);
}
function sortingPrompt(id) {
  const [, , fruit, bay] = sortingCase(id);
  return `Use the left arm to put the ${fruit} on the transfer plate, then use the right arm to move it into the ${bay === 'near' ? 'blue' : 'amber'} bay. Leave the other fruit in place.`;
}
function showSortingLayouts() {
  const select = el('layout');
  const sorting = el('goal').value === 'sorting';
  if (sorting) {
    for (const fruit of ['apple', 'orange']) {
      for (const bay of ['near', 'far']) {
        for (const number of ['00', '01']) addSortingLayout(`sort-holdout-${fruit}-${bay}_bay-${number}`);
      }
    }
  }
  for (const option of select.options) option.hidden = sorting !== Boolean(sortingCase(option.value));
  if (sorting !== Boolean(sortingCase(select.value))) select.selectedIndex = [...select.options].findIndex((option) => !option.hidden);
}
const modeLabels = {live: 'Dream-1 live', 'scripted reference': 'Scripted reference', 'offline fixture': 'Offline fixture'};
const escapeText = (text) => String(text ?? '').replace(/[&<>"']/g, (character) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[character]));

let state = {};
let selectedChunk = null;
let showPeak = false;
let selectedView = null;
let traceKey = '';
let submitting = false;
let reviewed = null;
let historyKey = '';
let requestedRun = new URLSearchParams(window.location.search).get('run');
let trainingBundle = null;
let publishedCheckpoint = null;
let repairError = null;

const activeRun = () => reviewed?.run ?? state.current ?? state.last_result;
const cameraNames = {top: 'Overhead', overview: '3D angle', left: 'Left wrist', right: 'Right wrist'};

function total(run) {
  const chunks = run.chunks ?? [];
  return {
    requests: chunks.length,
    steps: chunks.reduce((sum, chunk) => sum + chunk.executed_steps, 0),
    clipped: chunks.reduce((sum, chunk) => sum + (chunk.clipped_steps ?? 0), 0),
    lift: Math.max(0, ...chunks.map((chunk) => chunk.peak_lift_mm)),
  };
}

function showCamera(source, label) {
  const camera = el('camera');
  el('camera-label').textContent = label;
  if ((source ?? '') === (camera.dataset.source ?? '')) return;
  camera.dataset.source = source ?? '';
  if (source) {
    camera.src = source;
  } else {
    camera.removeAttribute('src');
    camera.hidden = true;
    el('camera-placeholder').hidden = false;
  }
}

function renderCamera() {
  const run = activeRun();
  if (!run) return;
  const scenes = {imported: 'Imported scene', workshop: 'Workshop', sorting: 'Sorting bench'};
  el('camera-scene').textContent = run.scene_name ?? scenes[run.scene] ?? 'Stock';
  const views = run.cameras ?? ['top'];
  const view = views.includes(selectedView) ? selectedView : ['workshop', 'imported'].includes(run.scene) ? 'overview' : 'top';
  el('camera-views').hidden = views.length < 2;
  for (const button of el('camera-views').querySelectorAll('[data-camera]')) {
    button.hidden = !views.includes(button.dataset.camera);
    button.setAttribute('aria-pressed', button.dataset.camera === view);
  }
  el('camera').alt = `${cameraNames[view]} view of the YAM simulator`;
  const query = new URLSearchParams({run_id: run.run_id, view});
  if (selectedChunk != null) {
    const chunk = run.chunks.find((item) => item.number === selectedChunk);
    const label = chunk.phase ?? `Response ${chunk.number}`;
    const exact = showPeak && chunk.has_peak_image;
    query.set('chunk', selectedChunk);
    if (exact) query.set('peak', '1');
    showCamera(`/api/result-image?${query}`,
      exact ? `Peak lift · ${label}` : `After ${label.toLowerCase()}`);
  } else if (state.current && !reviewed) {
    showCamera(state.preview ? `/api/preview?view=${view}&t=${Date.now()}` : null, state.preview ? 'Live' : 'Preparing');
  } else {
    showCamera(run.has_image === false ? null : `/api/result-image?${query}`, 'Final frame');
  }
}

function comparisonRow(run, label) {
  const totals = total(run);
  const measured = totals.requests > 0;
  const clipped = measured && totals.steps ? `${totals.clipped}/${totals.steps}` : '—';
  return `<tr><th scope="row">${label}</th><td>${run.actions_per_chunk ?? 48}</td>
    <td>${escapeText(verdicts[run.verdict] ?? run.verdict)}</td>
    <td>${measured ? totals.requests : '—'}</td><td>${clipped}</td>
    <td>${measured ? `${totals.lift} mm` : '—'}</td></tr>`;
}

function renderPolicyInputs(run) {
  const inputs = run?.request_inputs;
  el('policy-inputs').hidden = !inputs?.cameras?.length;
  el('open-policy-inputs').hidden = el('policy-inputs').hidden;
  if (el('policy-inputs').hidden) return;
  const seconds = (ms) => `${(ms / 1000).toFixed(2)} s`;
  const firstReply = inputs.first_reply_ms == null
    ? (run.verdict ? 'No successful policy reply' : 'Waiting for first policy reply')
    : `First reply at ${seconds(inputs.first_reply_ms)} (${seconds(inputs.first_successful_request_rtt_ms)} request round trip)`;
  el('policy-timing').textContent = `First request prepared at ${seconds(inputs.first_request_after_ms)} · ${firstReply} · ${inputs.replies ?? 0}/${inputs.attempts} replies`;
  const container = el('policy-frames');
  const key = `${run.run_id}/${inputs.snapshot_attempt}`;
  if (container.dataset.snapshot === key) return;
  container.dataset.snapshot = key;
  container.replaceChildren(...inputs.cameras.map((camera, index) => {
    const card = document.createElement('figure');
    const image = document.createElement('img');
    const query = new URLSearchParams({run_id: run.run_id, index, snapshot: inputs.snapshot_attempt});
    image.src = `/api/policy-camera?${query}`;
    image.alt = `Prepared frame for ${camera.key}`;
    image.loading = 'lazy';
    const caption = document.createElement('figcaption');
    caption.textContent = `${camera.key} · ${camera.frames} frames · ${camera.width} × ${camera.height}`;
    card.append(image, caption);
    return card;
  }));
}

function renderResult() {
  const run = activeRun();
  renderPolicyInputs(run);
  const previous = reviewed ? reviewed.previous : state.current ? null : state.previous_result;
  el('result').hidden = !run;
  if (!run) return;
  el('result').dataset.verdict = run.verdict ?? '';
  el('result-title').textContent = run.verdict === 'passed' ? run.milestone : verdicts[run.verdict] ?? 'Run in progress';
  el('result-mode').textContent = modeLabels[run.mode] ?? '';
  el('result-detail').textContent = run.error || run.fixture_note || run.diagnosis || run.milestone;
  if (run.api_error?.code) {
    el('result-detail').textContent += ` (${run.api_error.code}${run.api_error.reason ? ` · ${run.api_error.reason}` : ''})`;
  }
  if (run.verdict === 'unscored') el('result-detail').textContent = 'Inspect camera and joint evidence. This scene has no automatic goal rule.';
  if (run.region_goal && run.region_progress && !run.error) {
    const progress = run.region_progress;
    el('result-detail').textContent = `${run.region_goal.object} · ${progress.dwell_ticks}/${progress.required_ticks} consecutive ticks inside the region`;
  }
  el('result-detail').hidden = !el('result-detail').textContent || el('result-detail').textContent === el('result-title').textContent;
  const summary = run.mode === 'live' ? `“${run.prompt}” · ${run.model ?? 'Dream-1'} · ${run.scene_name ?? run.layout_id ?? 'stock'} · ${run.actions_per_chunk ?? 48} steps per response`
    : run.mode === 'scripted reference' ? 'Moves the simulated robot through a scripted trajectory.' : 'Checks rendering and scoring without model calls.';
  el('result-prompt').textContent = run.elapsed_seconds ? `${summary} · ${run.elapsed_seconds} s` : summary;
  el('stages').hidden = Boolean(run.error) || run.scene === 'imported';
  const observed = run.checks ?? {
    lifted: run.milestone?.endsWith('lifted from the table') || total(run).lift > 45,
    reached_bowl: run.milestone?.endsWith('reached the bowl') || run.verdict === 'passed',
    settled: run.verdict === 'passed',
  };
  const relay = ['relay', 'sorting'].includes(run.task);
  const stages = relay ? ['left_pickup', 'transfer', 'settled'] : ['lifted', 'reached_bowl', 'settled'];
  const labels = relay ? ['Left pickup', 'Plate transfer', 'Right placement'] : ['Lifted', 'Reached bowl', 'Settled in bowl'];
  [...el('stages').children].forEach((stage, index) => {
    stage.dataset.reached = Boolean(observed[stages[index]]);
    stage.textContent = labels[index];
  });
  el('retry-controls').hidden = (!reviewed && Boolean(state.current)) || run.mode !== 'live';
  el('compare-from-run').hidden = !run.verdict || relay || run.scene === 'imported' || run.model !== 'dreamlabs/dream-1-beta';
  el('test-fix').hidden = run.mode !== 'live' || run.verdict !== 'task_timeout' || relay || run.scene === 'imported';
  el('repair-error').hidden = repairError?.runId !== run.run_id;
  el('repair-error').textContent = repairError?.runId === run.run_id ? repairError.message : '';
  el('shorter').hidden = run.mode !== 'live' || run.verdict === 'api_error' || (run.actions_per_chunk ?? 48) <= 1;
  el('shorter').textContent = `Retry at ${Math.floor((run.actions_per_chunk ?? 48) / 2)} steps`;
  el('comparison').hidden = !previous;
  if (previous) {
    const descriptor = run.prompt === previous.prompt ? 'Same prompt and starting layout' : 'Edited prompt, same starting layout';
    el('comparison').innerHTML = `<p>${descriptor}. One trial per setting.</p>
      <div class="table-scroll"><table><thead><tr><th>Run</th><th>Step limit</th><th>Outcome</th>
      <th>Responses</th><th>Steps clipped</th><th>Peak lift</th></tr></thead><tbody>
      ${comparisonRow(previous, 'Original')}${comparisonRow(run, 'Retry')}</tbody></table></div>`;
  }
}

function renderChunk(chunk) {
  const container = el('chunk-details');
  if (!chunk) return;
  const rows = (chunk.joints ?? []).map((joint) => `<tr><th scope="row">${escapeText(joint.name)}</th><td>${joint.clipped}</td>
    <td>${joint.requested.toFixed(4)} ${joint.unit}</td><td>${joint.applied.toFixed(4)} ${joint.unit}</td></tr>`).join('');
  const direction = chunk.bowl_progress_mm < 0 ? 'farther from' : 'closer to';
  const execution = chunk.phase ? `${chunk.executed_steps} simulator steps` : `${chunk.rtt_ms} ms · ${chunk.executed_steps} of ${chunk.proposed_steps} proposed steps ran · ${chunk.clipped_steps} clipped`;
  container.innerHTML = `<p><strong>${escapeText(chunk.phase ?? `Response ${chunk.number}`)}</strong> · ${execution}</p>
    <p class="muted">${escapeText(chunk.milestone)}${chunk.bowl_progress_mm == null ? '' : ` · ${chunk.peak_lift_mm} mm peak lift ·
    ${chunk.target_moved_mm ?? chunk.apple_moved_mm} mm horizontal movement · ${Math.abs(chunk.bowl_progress_mm)} mm ${direction} ${run.task === 'sorting' ? 'target bay' : 'bowl'}`}</p>
    ${rows ? `<div class="table-scroll"><table><thead><tr><th>Joint</th><th>Clipped steps</th>
    <th>Requested target*</th><th>Applied target*</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="muted">*The largest correction for each joint in this response.</p>` : chunk.phase ? '' : '<p>No joint targets were clipped in the executed steps.</p>'}`;
}

function renderTrace() {
  const run = activeRun();
  const chunks = run?.chunks ?? [];
  const key = `${run?.run_id}/${chunks.length}/${selectedChunk}/${Boolean(state.current)}/${run?.verdict}`;
  if (key === traceKey) return;
  traceKey = key;

  el('evidence').hidden = chunks.length === 0;
  el('evidence-empty').hidden = chunks.length > 0;
  el('terminal').hidden = !run;
  el('output-empty').hidden = Boolean(run);
  if (!run) return;
  const totals = total(run);
  const reference = run.mode === 'scripted reference';
  const joints = new Map();
  for (const chunk of chunks) {
    for (const joint of chunk.joints ?? []) joints.set(joint.name, (joints.get(joint.name) ?? 0) + joint.clipped);
  }
  const mostClipped = [...joints].sort((first, second) => second[1] - first[1])[0];
  const topJoint = mostClipped ? ` · ${mostClipped[0]} clipped on ${mostClipped[1]} steps` : '';
  el('peak').hidden = totals.lift <= 0;
  el('evidence-title').textContent = reference ? 'Reference timeline' : 'Action timeline';
  el('show-timeline').textContent = el('evidence-title').textContent;
  el('clipping-legend').hidden = reference;
  el('trace-summary').textContent = reference
    ? `${totals.steps} simulator steps · ${totals.lift} mm peak ${taskName(run)} lift`
    : `${totals.clipped} of ${totals.steps} executed steps clipped · ${totals.lift} mm peak ${taskName(run)} lift${topJoint}`;
  if (run.scene === 'imported') el('trace-summary').textContent = `${totals.clipped} of ${totals.steps} executed steps clipped${topJoint}`;
  const output = el('output');
  const lines = [`${run.mode === 'live' ? run.model ?? 'dream-1-beta' : modeLabels[run.mode]} · ${run.prompt}`];
  for (const chunk of chunks) {
    const stepInfo = chunk.phase ? `${chunk.executed_steps} simulator steps` : `${chunk.rtt_ms} ms   ${chunk.executed_steps}/${chunk.proposed_steps} steps ran   ${chunk.clipped_steps} clipped`;
    lines.push(`${chunk.phase ?? `Response ${chunk.number}`}   ${stepInfo}${run.scene === 'imported' ? '' : `   peak lift ${chunk.peak_lift_mm} mm`}`);
  }
  if (run.verdict) lines.push(`${run.verdict === 'passed' ? run.milestone : verdicts[run.verdict]} · ${run.elapsed_seconds} s`);
  output.textContent = lines.join('\n');
  output.scrollTop = output.scrollHeight;
  if (!chunks.length) return;

  const selection = chunks.find((chunk) => chunk.number === selectedChunk) ?? chunks.at(-1);
  const timeline = el('timeline');
  const maxLift = Math.max(45, totals.lift);
  const focus = timeline.contains(document.activeElement) ? document.activeElement.dataset.chunk : null;
  timeline.innerHTML = chunks.map((chunk) => {
    const percent = chunk.executed_steps && !reference ? Math.round(100 * chunk.clipped_steps / chunk.executed_steps) : 0;
    const lift = Math.round(100 * chunk.peak_lift_mm / maxLift);
    const description = chunk.phase ? chunk.phase : `Response ${chunk.number}, ${percent}% clipped`;
    return `<button class="trace-button" data-chunk="${chunk.number}" aria-pressed="${chunk === selection}"
      aria-label="${escapeText(description)}, ${chunk.peak_lift_mm} mm lift">
      <span class="bars" aria-hidden="true"><i style="--bar:${percent}%"></i><i style="--bar:${lift}%"></i></span>
      ${chunk.number}</button>`;
  }).join('');
  if (focus) timeline.querySelector(`[data-chunk="${focus}"]`)?.focus({preventScroll: true});
  if (selectedChunk == null && !focus) timeline.scrollLeft = timeline.scrollWidth;
  el('follow').hidden = selectedChunk == null;
  el('follow').textContent = state.current && !reviewed ? 'Return to live camera' : 'Show final frame';
  renderChunk(selection);
}

function updateButtons() {
  for (const id of ['run', 'reference', 'retry', 'shorter']) {
    el(id).disabled = submitting || Boolean(state.current) || state.benchmark?.status === 'running';
  }
  el('reference').disabled ||= el('layout').value.startsWith('imported-');
  el('stop-run').hidden = !state.current || state.benchmark?.status === 'running';
  el('stop-run').disabled = state.current?.status === 'stopping';
}

async function startRun({reference = false, retry = false, shorter = false} = {}) {
  if (submitting || state.current || state.benchmark?.status === 'running') return;
  if (!reference && ((!shorter && !el('prompt').value.trim()) || (!retry &&
      (!el('actions').reportValidity() || !el('seconds').reportValidity())))) {
    el('status').textContent = 'Enter a prompt, step limit and time limit.';
    return;
  }
  submitting = true;
  updateButtons();
  el('status').textContent = 'Starting simulator…';
  const body = {prompt: el('prompt').value, reference, shorter, layout: el('layout').value, task: el('goal').value,
    retry_of: retry ? activeRun().run_id : null};
  if (!retry && !reference) {
    body.goal = importedGoals.get(body.layout) ?? null;
    body.actions = el('actions').valueAsNumber;
    body.seconds = el('seconds').valueAsNumber;
    body.model = el('model').value.trim();
  }
  try {
    const response = await fetch('/api/run', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.error);
    state.current = {run_id: result.run_id};
    reviewed = null;
    const url = new URL(location.href);
    url.searchParams.delete('run');
    history.replaceState(null, '', url);
    selectedChunk = null;
    showPeak = false;
    selectedView = null;
  } catch (error) {
    el('status').textContent = error.message;
  } finally {
    submitting = false;
    updateButtons();
  }
}

function renderHistory() {
  const recent = state.recent ?? [];
  const latest = recent[0];
  el('home-recent').hidden = !latest;
  if (latest && el('home-run').dataset.id !== latest.run_id) {
    const link = el('home-run');
    link.dataset.id = latest.run_id;
    link.href = `/?run=${encodeURIComponent(latest.run_id)}#judge`;
    link.textContent = `${latest.prompt} · ${latest.verdict === 'passed' ? 'Passed' : verdicts[latest.verdict] ?? latest.verdict} ↗`;
  }
  const example = state.benchmark?.repair?.source_run ?? recent.find((run) =>
    run.mode === 'live' && run.verdict === 'task_timeout' && ['apple', 'orange'].includes(taskName(run)))?.run_id;
  el('try-example').hidden = !example;
  if (example) el('try-example').href = `/?run=${encodeURIComponent(example)}#judge`;
  el('latest').hidden = !reviewed;
  const query = el('history-query').value.trim().toLowerCase();
  const filter = el('history-filter').value;
  const key = JSON.stringify([activeRun()?.run_id, recent.map((run) => run.run_id), query, filter]);
  if (key === historyKey) return;
  historyKey = key;
  const filtered = recent.filter((run) => {
    const matches = [run.prompt, run.model, run.scene_name, run.layout_id, run.task, modeLabels[run.mode]].join(' ').toLowerCase().includes(query);
    const outcome = filter === 'all' || run.verdict === filter || (filter === 'errors' && ['api_error', 'sim_error', 'canceled'].includes(run.verdict));
    return matches && outcome;
  });
  el('run-list').innerHTML = filtered.length ? filtered.map((run) => {
    const when = new Date(run.created_at);
    const date = Number.isNaN(when.getTime()) ? '' : new Intl.DateTimeFormat([], {dateStyle: 'medium', timeStyle: 'short'}).format(when);
    const verdict = run.verdict === 'passed' ? 'Passed' : verdicts[run.verdict] ?? run.verdict;
    return `<button class="run-card" data-run="${run.run_id}" aria-current="${activeRun()?.run_id === run.run_id}">
      <span class="run-card-copy"><strong>${escapeText(run.prompt || 'Apple into bowl')}</strong><small>${escapeText([modeLabels[run.mode], run.scene_name ?? run.layout_id, date].filter(Boolean).join(' · '))}</small></span>
      <span class="pill run-outcome" data-verdict="${escapeText(run.verdict)}">${escapeText(verdict)}</span><span class="run-open" aria-hidden="true">↗</span></button>`;
  }).join('') : `<p class="muted empty-notice">${recent.length ? 'No saved runs match these filters.' : 'Completed runs will appear here.'}</p>`;
}

async function poll() {
  try {
    const response = await fetch('/api/status');
    if (!response.ok) throw Error('Could not reach DreamCheck.');
    const next = await response.json();
    if (!reviewed && (next.current ?? next.last_result)?.run_id !== (state.current ?? state.last_result)?.run_id) {
      selectedChunk = null;
      showPeak = false;
      selectedView = null;
    }
    state = next;
    if (requestedRun) {
      const runId = requestedRun;
      requestedRun = null;
      await reviewRun(runId);
    }
    const current = state.current;
    el('station-state').textContent = current ? 'Simulation active' : 'Ready';
    if (!submitting) {
      el('status').textContent = current
        ? `${statuses[current.status]} · ${modeLabels[current.mode]}`
        : state.last_result ? 'Run complete' : 'Ready';
    }
    renderResult();
    renderTrace();
    renderCamera();
    renderHistory();
    window.renderBenchmark?.(state.benchmark, state.current, state.benchmark_history);
    updateButtons();
  } catch (error) {
    el('status').textContent = error.message;
    el('station-state').textContent = 'Disconnected';
  } finally {
    setTimeout(poll, 650);
  }
}

el('camera').onload = () => {
  el('camera').hidden = false;
  el('camera-placeholder').hidden = true;
};
el('open-policy-inputs').onclick = () => {
  const details = el('policy-inputs');
  details.open = true;
  const summary = details.querySelector('summary');
  summary.focus({preventScroll: true});
  summary.scrollIntoView({block: 'center'});
};
el('camera').onerror = () => {
  el('camera').hidden = true;
  el('camera-placeholder').hidden = false;
};
el('actions').oninput = () => {
  const actions = el('actions').valueAsNumber;
  const rate = el('model').value.trim().startsWith('dream-1-ft:') ? 10 : 20;
  el('cadence').textContent = Number.isFinite(actions) ? `Up to ${(actions / rate).toFixed(2)} s of simulated motion per response` : '';
};
el('model').oninput = el('actions').oninput;
el('goal').onchange = () => {
  const task = el('goal').value;
  showSortingLayouts();
  el('prompt').value = task === 'sorting' ? sortingPrompt(el('layout').value) : defaultPrompt(task);
  if (['relay', 'sorting'].includes(task) && el('seconds').valueAsNumber < 90) el('seconds').value = '90';
};
el('try-offline').onclick = () => {
  el('latest').click();
  el('goal').value = 'relay';
  el('layout').value = 'stock';
  el('goal').onchange();
  el('layout').onchange();
  window.openWorkspace('judge');
  el('reference').focus();
};
el('layout').onchange = (event) => {
  const value = el('layout').value;
  const imported = value.startsWith('imported-');
  if (event && sortingCase(value) && el('goal').value === 'sorting') {
    el('prompt').value = sortingPrompt(value);
  }
  el('goal').disabled = imported;
  el('scene-note').hidden = !imported && value !== 'workshop';
  const goal = importedGoals.get(value);
  el('scene-note').textContent = imported ? (goal
    ? `Goal: ${goal.object} in the region at ${goal.destination.split(':').slice(1).join(':')} for ${(goal.dwell_ticks * .05).toFixed(2)} s.`
    : 'Open this scene in Import to set an optional goal region.')
    : 'Physical blocks, puck, and parts tray. DreamCheck scores only the selected fruit.';
  updateButtons();
};
window.addSceneOption = (id, name) => {
  const select = el('layout');
  if (![...select.options].some((option) => option.value === id)) select.add(new Option(name, id));
};
window.useImportedScene = (id, name, goal) => {
  el('latest').click();
  window.addSceneOption(id, name);
  importedGoals.set(id, goal);
  if (el('goal').value === 'sorting') el('goal').value = 'apple';
  showSortingLayouts();
  el('layout').value = id;
  if (!['dreamlabs/dream-1-beta', 'dreamlabs/dream-1-beta-flash'].includes(el('model').value)) el('model').value = 'dreamlabs/dream-1-beta';
  el('layout').onchange();
  window.openWorkspace('judge');
  el('prompt').focus();
};
el('camera-views').onclick = (event) => {
  const button = event.target.closest('[data-camera]');
  if (!button) return;
  selectedView = button.dataset.camera;
  renderCamera();
};
el('run').onclick = () => startRun();
el('reference').onclick = () => startRun({reference: true});
el('stop-run').onclick = async () => {
  if (!state.current) return;
  el('stop-run').disabled = true;
  try {
    const response = await fetch('/api/stop', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: state.current.run_id})});
    if (response.ok) el('status').textContent = 'Stopping simulator…';
  } catch (error) {
    el('status').textContent = error.message;
  }
};
el('preset').onclick = () => {
  const task = el('goal').value;
  el('prompt').value = task === 'sorting' ? sortingPrompt(el('layout').value)
    : task === 'relay' ? defaultPrompt(task) : demoPromptFor(task);
};
el('retry').onclick = () => startRun({retry: true});
el('shorter').onclick = () => startRun({retry: true, shorter: true});
el('compare-from-run').onclick = () => window.comparePrompt?.(activeRun().prompt, taskName(activeRun()));
el('test-fix').onclick = async () => {
  const button = el('test-fix');
  const runId = activeRun().run_id;
  button.disabled = true;
  repairError = null;
  try {
    await window.planRepair(runId);
  } catch (error) {
    repairError = {runId, message: error.message};
    renderResult();
  } finally {
    button.disabled = false;
  }
};
el('timeline').onclick = (event) => {
  const button = event.target.closest('[data-chunk]');
  if (!button) return;
  selectedChunk = Number(button.dataset.chunk);
  showPeak = false;
  renderTrace();
  renderCamera();
};
el('follow').onclick = () => {
  selectedChunk = null;
  showPeak = false;
  renderTrace();
  renderCamera();
};
el('peak').onclick = () => {
  const chunks = activeRun()?.chunks ?? [];
  const peak = chunks.reduce((best, chunk) => !best || chunk.peak_lift_mm > best.peak_lift_mm ? chunk : best, null);
  if (!peak) return;
  selectedChunk = peak.number;
  showPeak = true;
  renderTrace();
  renderCamera();
  el('timeline').querySelector(`[data-chunk="${peak.number}"]`)?.focus({preventScroll: true});
  if (window.matchMedia('(max-width: 640px)').matches) el('camera-title').scrollIntoView({block: 'start'});
};
async function reviewRun(runId, navigate = false) {
  try {
    const response = await fetch(`/api/run?run_id=${encodeURIComponent(runId)}`);
    if (!response.ok) throw Error('Could not load the saved run.');
    reviewed = await response.json();
    if (navigate) window.openWorkspace?.('judge');
    const url = new URL(location.href);
    url.searchParams.set('run', runId);
    history.replaceState(null, '', url);
    selectedChunk = null;
    showPeak = false;
    selectedView = null;
    el('goal').value = reviewed.run.scene === 'imported' ? 'apple' : taskName(reviewed.run);
    if (reviewed.run.scene === 'imported') {
      window.addSceneOption(reviewed.run.layout_id, reviewed.run.scene_name ?? 'Imported scene');
      importedGoals.set(reviewed.run.layout_id, reviewed.run.region_goal ?? null);
    }
    if (reviewed.run.task === 'relay') addRelayLayout(reviewed.run.layout_id);
    if (reviewed.run.task === 'sorting') addSortingLayout(reviewed.run.layout_id);
    showSortingLayouts();
    el('layout').value = reviewed.run.layout_id ?? 'stock';
    if (!el('layout').value) el('layout').value = 'stock';
    if (reviewed.run.mode === 'live') {
      el('prompt').value = reviewed.run.prompt;
      el('model').value = reviewed.run.model ?? 'dreamlabs/dream-1-beta';
      el('actions').value = reviewed.run.actions_per_chunk ?? 48;
      el('seconds').value = reviewed.run.seconds_limit ?? 60;
      el('actions').oninput();
    }
    el('layout').onchange();
    renderResult();
    renderTrace();
    renderCamera();
    renderHistory();
  } catch (error) {
    el('status').textContent = error.message;
  }
}
el('run-list').onclick = (event) => {
  const button = event.target.closest('[data-run]');
  if (button) reviewRun(button.dataset.run, true);
};
el('history-query').oninput = renderHistory;
el('history-filter').onchange = renderHistory;
el('latest').onclick = () => {
  reviewed = null;
  const url = new URL(location.href);
  url.searchParams.delete('run');
  history.replaceState(null, '', url);
  selectedChunk = null;
  showPeak = false;
  selectedView = null;
  renderResult();
  renderTrace();
  renderCamera();
  renderHistory();
};
el('actions').oninput();
poll();

async function loadTraining() {
  try {
    const picker = el('training-bundle');
    const query = picker.value ? `?bundle=${encodeURIComponent(picker.value)}` : '';
    const response = await fetch(`/api/training${query}`);
    if (!response.ok) throw Error('Could not load training exports.');
    const {bundle, bundles = [], evaluation, baseline} = await response.json();
    const changed = trainingBundle?.id !== bundle?.id;
    trainingBundle = bundle;
    if (!bundle) {
      el('training-empty').textContent = 'Create a dataset with python -m dreamcheck train prepare.';
      el('training-empty').hidden = false;
      return;
    }
    el('training-empty').hidden = true;
    el('training-bundle-control').hidden = bundles.length < 2;
    picker.replaceChildren(...bundles.map((entry) => {
      const name = entry.task === defaultPrompt('relay') ? 'Two-arm relay'
        : entry.task === 'put apple into the bowl' ? 'Apple placement'
        : entry.task === 'Sort the requested fruit through the plate into the requested bay' ? 'Two-arm fruit sorting'
        : entry.task;
      return new Option(name, entry.id);
    }));
    picker.value = bundle.id;
    el('training-task').textContent = bundle.task;
    const {train, holdout} = bundle.validation;
    el('training-state').textContent = train.ok && holdout.ok ? 'Validated by Dream Labs SDK' : 'Validation failed';
    el('training-counts').textContent = `${train.episodes} training episodes · ${holdout.episodes} held out · ${bundle.sample_rate_hz} Hz · 3 cameras`;
    el('training-data').hidden = false;
    const selectedDemo = changed ? '' : el('training-episode').value;
    const demos = bundle.attempts.filter((attempt) => attempt.passed);
    el('training-episode').innerHTML = demos.map((attempt) =>
      `<option value="${escapeText(attempt.layout)}">${escapeText(attempt.layout)} · passed · ${attempt.frames} frames</option>`).join('');
    if (selectedDemo && demos.some((attempt) => attempt.layout === selectedDemo)) el('training-episode').value = selectedDemo;
    el('training-episode').onchange();
    el('training-upload').textContent = bundle.upload
      ? `Uploaded training split · ${bundle.upload.id}` : 'The validated training split is ready to upload.';
    el('dataset-status').hidden = !bundle.upload;
    if (changed) {
      el('training-job').value = bundle.job?.id ?? '';
      publishedCheckpoint = bundle.job?.checkpoint ?? null;
      el('training-cloud').textContent = publishedCheckpoint ? `Published · ${publishedCheckpoint}` : '';
      el('training-progress').hidden = true;
      el('use-checkpoint').hidden = !publishedCheckpoint;
    }
    el('baseline-card').hidden = !baseline;
    if (baseline) {
      const trials = baseline.pairs.map((pair) => pair.runs.base).filter(Boolean);
      const scored = trials.filter((run) => ['passed', 'task_timeout'].includes(run.verdict));
      const total = baseline.layouts.length * baseline.repeats;
      el('baseline-summary').textContent = `${scored.length}/${total} valid trials · ${scored.filter((run) => run.verdict === 'passed').length} passes · ${baseline.status}`;
      const stages = [['left_pickup', 'Left pickup'], ['transfer', 'Plate transfer'], ['right_pickup', 'Right pickup'], ['settled', 'Final placement']];
      el('baseline-stages').replaceChildren(...stages.map(([phase, name]) => {
        const failures = scored.filter((run) => run.failure_phase === phase);
        const item = document.createElement('div');
        const label = document.createElement('span');
        label.textContent = name;
        const count = document.createElement('strong');
        count.textContent = failures.length;
        item.append(label, count);
        if (failures.length && failures[0].run_id) {
          const link = document.createElement('a');
          link.href = `/?run=${encodeURIComponent(failures[0].run_id)}#judge`;
          link.textContent = 'Inspect example';
          link.setAttribute('aria-label', `Inspect a ${name.toLowerCase()} failure`);
          item.append(link);
        }
        return item;
      }));
      const last = trials.at(-1);
      el('baseline-run').hidden = !last?.run_id;
      if (last?.run_id) el('baseline-run').href = `/?run=${encodeURIComponent(last.run_id)}#judge`;
    }
    el('evaluation-summary').textContent = 'No matched evaluation for this dataset yet.';
    if (evaluation) {
      const scored = (policy) => evaluation.pairs
        .map((pair) => pair.runs[policy])
        .filter((run) => ['passed', 'task_timeout', 'wrong_bay', 'wrong_object_moved'].includes(run?.verdict));
      el('evaluation-summary').textContent = evaluation.status === 'complete'
        ? `The latest comparison scored ${evaluation.passes.base}/${evaluation.pairs.length} for the base model and ${evaluation.passes.fine_tuned}/${evaluation.pairs.length} for the fine-tune.`
        : `Evaluation ${evaluation.status} · ${scored('base').length} base and ${scored('fine_tuned').length} fine-tuned trials scored of ${evaluation.layouts.length * evaluation.repeats} per model.`;
      if (evaluation.status === 'complete' && evaluation.task === 'relay') {
        const base = evaluation.pairs.filter((pair) => pair.runs.base.checks?.transfer).length;
        const tuned = evaluation.pairs.filter((pair) => pair.runs.fine_tuned.checks?.transfer).length;
        el('evaluation-summary').textContent += ` Plate transfers completed out of ${evaluation.pairs.length} trials: ${base} for base and ${tuned} for the fine-tune.`;
      }
    }
  } catch (error) {
    el('training-empty').textContent = error.message;
    el('training-empty').hidden = false;
  }
}

el('training-episode').onchange = () => {
  const video = el('training-video');
  const query = new URLSearchParams({layout: el('training-episode').value, bundle: trainingBundle.id});
  const source = `/api/training-video?${query}`;
  if (video.getAttribute('src') !== source) video.src = source;
};

async function checkTraining(kind, id) {
  if (!id) return;
  const bundleId = trainingBundle?.id;
  el('training-cloud').textContent = 'Checking Dream Labs…';
  try {
    const response = await fetch(`/api/training-status?${kind}=${encodeURIComponent(id)}`);
    const result = await response.json();
    if (trainingBundle?.id !== bundleId) return;
    if (!response.ok) throw Error(result.error);
    el('training-cloud').textContent = [result.status, result.progress?.phase, result.error, result.fine_tuned_model].filter(Boolean).join(' · ');
    if (kind === 'job') {
      const progress = el('training-progress');
      const step = result.progress?.step;
      const total = result.progress?.total_steps ?? result.hyperparameters?.steps;
      progress.hidden = !Number.isFinite(step) || !Number.isFinite(total) || total <= 0;
      if (!progress.hidden) {
        progress.max = total;
        progress.value = step;
        el('training-cloud').textContent += ` · ${step}/${total} steps`;
      }
      publishedCheckpoint = result.dataset === trainingBundle?.upload?.name ? result.fine_tuned_model : null;
      el('use-checkpoint').hidden = !publishedCheckpoint;
    }
  } catch (error) {
    if (trainingBundle?.id === bundleId) el('training-cloud').textContent = error.message;
  }
}

el('dataset-status').onclick = () => checkTraining('dataset', trainingBundle?.upload?.id);
el('job-status').onclick = () => checkTraining('job', el('training-job').value.trim());
el('use-checkpoint').onclick = () => {
  const relay = trainingBundle.task === defaultPrompt('relay');
  const sorting = trainingBundle.attempts.some((attempt) => sortingCase(attempt.layout));
  el('model').value = publishedCheckpoint;
  const layout = sorting ? 'sort-holdout-apple-near_bay-00' : relay ? 'relay-holdout-00' : 'holdout-left';
  addRelayLayout(layout);
  addSortingLayout(layout);
  el('goal').value = sorting ? 'sorting' : relay ? 'relay' : 'apple';
  el('layout').value = layout;
  el('goal').onchange();
  el('layout').onchange();
  if (!sorting) el('prompt').value = trainingBundle.task;
  el('actions').value = 12;
  el('actions').oninput();
  el('model').closest('details').open = true;
  window.openWorkspace?.('judge');
  el('prompt').focus();
};
el('baseline-refresh').onclick = loadTraining;
el('training-bundle').onchange = loadTraining;
window.refreshTraining = loadTraining;
