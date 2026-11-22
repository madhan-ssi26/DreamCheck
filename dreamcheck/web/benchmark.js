const bench = (id) => document.getElementById(`bench-${id}`);
let displayedBenchmark = null;
let benchmarkKey = '';
let comparisonRequest = false;
let cancellationRequested = false;
let inspectedPair = null;
let showPairPeak = false;
let latestBenchmark = null;
let savedBenchmark = null;
let archiveKey = '';
let requestedComparison = new URLSearchParams(window.location.search).get('comparison');
let pendingRepair = null;
let repairReportId = null;
let reportRequest = 0;

const benchStatusLabels = {running: 'Running', complete: 'Complete', canceled: 'Canceled', interrupted: 'Interrupted'};
const layoutLabels = {stock: 'Stock scene', workshop: 'Workshop', 'workshop-left': 'Workshop · left', 'workshop-right': 'Workshop · right', 'holdout-left': 'Holdout · left', 'holdout-right': 'Holdout · right', 'holdout-far': 'Holdout · far'};
const resultLabels = {passed: 'Passed', task_timeout: 'Timed out', wrong_bay: 'Wrong bay', wrong_object_moved: 'Unrequested fruit moved',
  api_error: 'API error', sim_error: 'Simulator error', canceled: 'Stopped'};
const scoredVerdicts = ['passed', 'task_timeout', 'wrong_bay', 'wrong_object_moved'];
const phaseLabels = {pickup: 'Pickup', transport: 'Transport', release: 'Release', left_pickup: 'Left pickup', transfer: 'Plate transfer',
  right_pickup: 'Right pickup', settled: 'Right placement', wrong_bay: 'Wrong bay', wrong_object_moved: 'Unrequested fruit moved',
  goal_region: 'Region dwell', completed: 'Completed'};
const taskLabels = {apple: 'Apple into bowl', orange: 'Orange into bowl', relay: 'Two-arm apple relay',
  sorting: 'Two-arm fruit sorting', imported: 'Imported scene goal'};
function layoutLabel(id, sceneName = null) {
  if (sceneName) return sceneName;
  const match = sortingCase(id);
  if (!match) return layoutLabels[id] ?? id.replaceAll('-', ' ');
  const [, split, fruit, bay, number] = match;
  return `${fruit} → ${bay === 'near' ? 'blue' : 'amber'} bay · ${split} ${number}`;
}

function trialLink(runId) {
  const query = new URLSearchParams({run: runId, from_comparison: displayedBenchmark.id});
  const bundle = new URLSearchParams(location.search).get('bundle');
  if (bundle) query.set('bundle', bundle);
  return `/?${query}#judge`;
}

function toggleVariable() {
  const variable = bench('variable').value;
  bench('prompt-b').hidden = variable !== 'prompt';
  bench('cadence-a').hidden = variable !== 'cadence';
  bench('cadence-b').hidden = variable !== 'cadence';
  bench('model-b').hidden = variable !== 'checkpoint';
  bench('b').required = variable === 'prompt';
  bench('b').disabled = variable !== 'prompt';
  for (const field of ['actions-a', 'actions-b']) bench(field).disabled = variable !== 'cadence';
  bench('model').required = variable === 'checkpoint';
  bench('model').disabled = variable !== 'checkpoint';
}

function resultCell(pair, slot, active, running) {
  const run = pair.runs[slot];
  if (!run) {
    const isActive = active?.slot === slot && active.layout === pair.layout && active.repeat === pair.repeat;
    return `<td class="bench-cell">${isActive ? 'Running…' : running ? 'Queued' : '—'}</td>`;
  }
  const rate = run.executed_steps ? ` · ${run.clipped_steps}/${run.executed_steps} clipped` : '';
  const lift = run.peak_lift_mm;
  const stage = phaseLabels[run.failure_phase];
  return `<td class="bench-cell" data-verdict="${escapeText(run.verdict)}">
    <a href="${escapeText(trialLink(run.run_id))}" title="Open ${slot} simulator evidence">${escapeText(resultLabels[run.verdict] ?? run.verdict)}</a>
    <small>${escapeText(run.error || run.diagnosis || run.milestone)}</small>
    ${stage && run.verdict !== 'passed' ? `<small>Stage · ${stage}</small>` : ''}
    ${run.api_error?.code ? `<small>${escapeText(run.api_error.code)} · ${escapeText(run.api_error.reason)}</small>` : ''}
    ${lift != null ? `<small>${lift} mm peak lift${rate}</small>
      <div class="bench-lift" aria-hidden="true"><i style="width:${Math.min(100, lift / 1.5)}%"></i></div>` : ''}</td>`;
}

function configLabel(benchmark, slot) {
  const setting = benchmark.settings[slot];
  if (benchmark.variable === 'prompt') return `“${escapeText(setting.prompt)}”`;
  if (benchmark.variable === 'cadence') return `${setting.actions} steps per response`;
  const aids = [setting.assist?.stage_gate ? 'stage gate' : '', setting.assist?.stride > 1 ? `plan stride ${setting.assist.stride}` : ''].filter(Boolean);
  if (benchmark.variable === 'assist' && !aids.length) aids.push('plain policy loop');
  return `${escapeText(setting.model)} · ${setting.actions} steps per response${aids.length ? ` · ${aids.join(' · ')}` : ''}`;
}

window.renderBenchmark = (latest, current, archive = []) => {
  latestBenchmark = latest;
  if (requestedComparison) {
    const id = requestedComparison;
    requestedComparison = null;
    if (id !== latest?.id) openComparison(id);
  }
  const planningRepair = pendingRepair && (!repairReportId || latest?.id !== repairReportId);
  const benchmark = planningRepair ? null : savedBenchmark ?? latest;
  if (displayedBenchmark?.id !== benchmark?.id) {
    cancellationRequested = false;
    inspectedPair = null;
    showPairPeak = false;
    bench('inspector').hidden = true;
  }
  displayedBenchmark = benchmark;
  const running = benchmark?.status === 'running';
  const working = latest?.status === 'running';
  const heldout = benchmark?.source === 'evaluation';
  bench('setup-title').textContent = heldout ? 'Evaluation' : 'Comparison setup';
  bench('form').hidden = Boolean(pendingRepair || heldout);
  bench('evaluation-tools').hidden = !heldout;
  bench('start').disabled = Boolean(working || current || comparisonRequest);
  document.getElementById('repair-start').disabled = Boolean(working || current || comparisonRequest);
  bench('stop').hidden = !working;
  bench('running-actions').hidden = !working || Boolean(planningRepair);
  bench('stop').disabled = cancellationRequested;
  bench('follow').hidden = !working;
  bench('archive').hidden = archive.length < 2;
  const savedKey = `${savedBenchmark?.id}/${archive.map((item) => `${item.id}/${item.status}`).join(',')}`;
  if (archiveKey !== savedKey) {
    archiveKey = savedKey;
    const names = {prompt: 'Prompt', cadence: 'Step limit', checkpoint: 'Checkpoint', assist: 'Stage gate'};
    bench('archive-list').innerHTML = [{id: '', label: 'Latest comparison'}, ...archive.filter((item) => item.id !== latest?.id)
      .map((item) => ({id: item.id, label: `${names[item.variable]} · ${benchStatusLabels[item.status]} · ${new Date(item.started_at).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}`}))]
      .map((item) => `<button class="secondary" data-report="${item.id}" aria-pressed="${item.id === (savedBenchmark?.id ?? '')}">${escapeText(item.label)}</button>`).join('');
  }
  bench('label').textContent = benchmark ? benchStatusLabels[benchmark.status] ?? benchmark.status : '';
  bench('empty').hidden = Boolean(benchmark || planningRepair);
  el('repair-source').hidden = !planningRepair;
  if (!benchmark) {
    bench('report').hidden = true;
    bench('trials').hidden = true;
    benchmarkKey = '';
    const busy = 'A comparison is already running.';
    if (planningRepair && working && !comparisonRequest) bench('status').textContent = busy;
    else if (bench('status').textContent === busy) bench('status').textContent = '';
    return;
  }
  const finished = benchmark.pairs.reduce((sum, pair) => sum + Object.keys(pair.runs).length, 0);
  const total = benchmark.pairs.length * 2;
  if (!comparisonRequest) {
    const active = benchmark.active;
    const location = active ? `${layoutLabel(active.layout, benchmark.repair?.scene_name)} · ${active.slot} · repeat ${active.repeat}` : '';
    bench('status').textContent = benchmark.error || (cancellationRequested && running
      ? 'Stopping after the current request returns…' : `${finished}/${total} trial results saved${location ? ` · ${location}` : ''}`);
  }
  const key = `${benchmark.id}/${benchmark.status}/${finished}/${benchmark.active?.run_id}`;
  if (key === benchmarkKey) return;
  benchmarkKey = key;
  const {summary} = benchmark;
  const delta = summary.B.passes - summary.A.passes;
  const conclusion = !summary.matched ? 'Waiting for completed pairs' :
    delta === 0 ? 'Same completion count' : `Candidate ${delta > 0 ? 'gained' : 'lost'} ${Math.abs(delta)} ${Math.abs(delta) === 1 ? 'pass' : 'passes'}`;
  const stageRows = Object.keys(summary.A.phases ?? {}).map((key) => `<tr><th scope="row">${phaseLabels[key]}</th>
    <td>${summary.A.phases?.[key] ?? '—'}</td><td>${summary.B.phases?.[key] ?? '—'}</td></tr>`).join('');
  const liftSummary = summary.A.mean_peak_lift_mm != null && summary.B.mean_peak_lift_mm != null
    ? `Mean peak lift across matched pairs · A ${summary.A.mean_peak_lift_mm} mm · B ${summary.B.mean_peak_lift_mm} mm`
    : '';
  const rows = benchmark.pairs.map((pair, index) => `<tr><th scope="row">${escapeText(layoutLabel(pair.layout, benchmark.repair?.scene_name))}<br>
    <small>Repeat ${pair.repeat}</small> ${canInspect(pair) ? `<button class="text-button" data-inspect="${index}">Compare frames</button>` : ''}
    </th>${resultCell(pair, 'A', benchmark.active, running)}
    ${resultCell(pair, 'B', benchmark.active, running)}</tr>`).join('');
  const sharedPrompt = Object.keys(benchmark.layout_prompts ?? {}).length
    ? 'Each scene supplies the same fruit and bay prompt to both checkpoints.'
    : escapeText(benchmark.settings.A.prompt);
  bench('report').innerHTML = `
    <div class="section-heading"><h2>${heldout ? 'Held-out checkpoint evaluation' : 'Comparison results'}</h2><div class="action-setting">
    <a href="/api/benchmark?id=${benchmark.id}&format=csv" download>Download CSV</a>
    <a href="/api/benchmark?id=${benchmark.id}" download>JSON</a></div></div>
    <div class="bench-scoreboard">
      <div class="bench-score"><strong>${summary.A.passes}/${summary.matched}</strong> Baseline passes</div>
      <div class="bench-score"><strong>${summary.B.passes}/${summary.matched}</strong> Candidate passes</div>
      <div class="bench-score"><strong>${summary.matched}/${benchmark.pairs.length}</strong> Matched pairs</div>
    </div>
    ${benchmark.repair ? `<p><strong>${phaseLabels[benchmark.repair.phase]} fix · ${conclusion}</strong> · <a href="/?run=${benchmark.repair.source_run}#judge">Original failure</a></p>` : ''}
    ${liftSummary ? `<p class="muted">${liftSummary}</p>` : ''}
    <p class="bench-configs"><strong>A</strong> ${configLabel(benchmark, 'A')}<br><strong>B</strong> ${configLabel(benchmark, 'B')}</p>
    ${heldout ? `<p class="bench-configs">${sharedPrompt}</p>` : ''}
    <p class="muted">${taskLabels[benchmark.task ?? 'apple']} · ${benchmark.seconds} ${benchmark.time_basis === 'simulated' ? 'simulated ' : ''}s per trial.${benchmark.settings.A.assist?.stage_gate || benchmark.settings.B.assist?.stage_gate ? ' The stage gate parks the idle arm using the plate-transfer check from the simulator.' : ''}
      Pass rates include pairs with two valid verdicts.
      ${benchmark.order ? ` ${escapeText(benchmark.order)}.` : ''}</p>`;
  bench('trials').innerHTML = `
    <h2>Trial results</h2>
    ${summary.A.phases ? `<details class="setup-options" ${benchmark.repair || heldout ? 'open' : ''}><summary>Where attempts stopped</summary>
      <table class="phase-counts"><thead><tr><th>Stage</th><th>A · Baseline</th><th>B · Candidate</th></tr></thead><tbody>${stageRows}</tbody></table></details>` : ''}
    <div class="table-scroll"><table><thead><tr><th>Starting scene</th><th>A&nbsp;·&nbsp;Baseline</th><th>B&nbsp;·&nbsp;Candidate</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  bench('report').hidden = false;
  bench('trials').hidden = false;
  if (!inspectedPair) {
    let example = benchmark.pairs.findIndex((pair) => canInspect(pair) &&
      (pair.runs.A.verdict === 'passed') !== (pair.runs.B.verdict === 'passed'));
    if (example === -1) example = benchmark.pairs.findIndex(canInspect);
    if (example !== -1) inspectPair(example);
  }
};

function canInspect(pair) {
  return ['A', 'B'].every((slot) => scoredVerdicts.includes(pair.runs[slot]?.verdict) && pair.runs[slot].has_image !== false);
}

async function inspectPair(index, scroll = false) {
  const pair = displayedBenchmark.pairs[index];
  if (!canInspect(pair)) return;
  const selection = {...pair, runs: {...pair.runs}};
  inspectedPair = selection;
  renderPairFrames();
  for (const slot of ['A', 'B']) {
    const run = selection.runs[slot];
    if ('peak_chunk' in run && 'cameras' in run) continue;
    try {
      const response = await fetch(`/api/run?run_id=${encodeURIComponent(run.run_id)}`);
      if (!response.ok) continue;
      const details = await response.json();
      const peak = (details.run.chunks ?? []).reduce((best, chunk) => !best || chunk.peak_lift_mm > best.peak_lift_mm ? chunk : best, null);
      selection.runs[slot] = {...run, peak_chunk: peak?.number, has_peak_image: peak?.has_peak_image,
        cameras: details.run.cameras ?? ['top']};
    } catch {
      // The final frame stays available even if the earlier trace cannot load.
    }
  }
  if (inspectedPair !== selection) return;
  renderPairFrames();
  if (scroll) bench('inspector').scrollIntoView({behavior: 'smooth', block: 'nearest'});
}

function renderPairFrames() {
  if (!inspectedPair) return;
  bench('inspector').hidden = false;
  bench('inspector-title').textContent = `${layoutLabel(inspectedPair.layout, displayedBenchmark.repair?.scene_name)} · repeat ${inspectedPair.repeat}`;
  const prompt = displayedBenchmark.layout_prompts?.[inspectedPair.layout];
  bench('pair-prompt').textContent = prompt ?? '';
  bench('pair-prompt').hidden = !prompt;
  bench('view-final').setAttribute('aria-pressed', !showPairPeak);
  bench('view-peak').setAttribute('aria-pressed', showPairPeak);
  bench('view-peak').hidden = displayedBenchmark.task === 'imported';
  const switcher = bench('camera');
  const views = (inspectedPair.runs.A.cameras ?? ['top']).filter((view) => (inspectedPair.runs.B.cameras ?? ['top']).includes(view));
  switcher.hidden = views.length < 2;
  if (!views.includes(switcher.value)) switcher.value = 'top';
  bench('frames').innerHTML = ['A', 'B'].map((slot) => {
    const run = inspectedPair.runs[slot];
    const query = new URLSearchParams({run_id: run.run_id, view: switcher.value});
    let label = 'Final frame';
    if (showPairPeak && run.peak_chunk) {
      query.set('chunk', run.peak_chunk);
      if (run.has_peak_image) query.set('peak', '1');
      label = run.has_peak_image ? 'Peak lift' : 'End of peak-lift response';
    }
    return `<figure class="bench-frame"><img src="/api/result-image?${query}" alt="${slot} ${label.toLowerCase()}">
      <figcaption><strong>${slot} · ${label}</strong> · ${run.peak_lift_mm == null ? '' : `${run.peak_lift_mm} mm peak lift · `}
      <a href="${escapeText(trialLink(run.run_id))}">Open action timeline</a></figcaption></figure>`;
  }).join('');
}

async function benchmarkRequest(path, body) {
  const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw Error(result.error || 'The comparison is no longer running.');
  return result;
}

bench('form').onsubmit = async (event) => {
  event.preventDefault();
  if (comparisonRequest) return;
  comparisonRequest = true;
  bench('start').disabled = true;
  bench('status').textContent = 'Preparing the comparison…';
  try {
    savedBenchmark = null;
    await benchmarkRequest('/api/benchmark', {
      task: bench('goal').value,
      variable: bench('variable').value, prompt_a: bench('a').value, prompt_b: bench('b').value,
      actions_a: bench('actions-a').valueAsNumber, actions_b: bench('actions-b').valueAsNumber,
      model_b: bench('model').value.trim(), scenes: bench('scenes').value,
      repeats: bench('repeats').valueAsNumber, seconds: bench('seconds').valueAsNumber,
    });
  } catch (error) {
    bench('status').textContent = error.message;
    bench('start').disabled = false;
  } finally {
    comparisonRequest = false;
  }
};

function showRepair(plan) {
  pendingRepair = plan;
  repairReportId = null;
  document.getElementById('repair-setup').hidden = !plan;
  bench('status').textContent = '';
  if (!plan) {
    window.renderBenchmark(latestBenchmark, state.current, state.benchmark_history);
    return;
  }
  const source = encodeURIComponent(plan.source_run);
  el('repair-source-link').href = `/?run=${source}#judge`;
  el('repair-source-detail').textContent = [layoutLabel(plan.layouts[0], plan.scene_name), plan.source_milestone].filter(Boolean).join(' · ');
  el('repair-source-frame').hidden = plan.has_image === false;
  if (plan.has_image !== false) el('repair-source-image').src = `/api/result-image?run_id=${source}`;
  document.getElementById('repair-reason').textContent = plan.task === 'imported'
    ? 'The object did not finish the required dwell. Both prompts will use the saved scene and goal.'
    : `Observed failure · ${phaseLabels[plan.phase]}. Edit how the prompt handles this stage.`;
  document.getElementById('repair-original').textContent = plan.settings.A.prompt;
  el('repair-candidate').value = plan.settings.B.prompt;
  document.getElementById('repair-scope').value = 'source';
  const suite = plan.layouts.length === 1 ? 'Same scene' : `${plan.layouts.length} scenes`;
  document.querySelector('#repair-scope option[value="suite"]').textContent = `${suite} × 3 repeats per setting`;
  const twoArm = ['relay', 'sorting'].includes(plan.task) && !plan.settings.A.assist?.stage_gate;
  el('repair-fix-row').hidden = !twoArm;
  el('repair-fix').value = twoArm ? 'gate' : 'prompt';
  renderRepairScope();
  window.renderBenchmark(latestBenchmark, state.current, state.benchmark_history);
}

const repairUsesGate = () => !el('repair-fix-row').hidden && el('repair-fix').value === 'gate';

function renderRepairScope() {
  if (!pendingRepair) return;
  const plan = pendingRepair;
  const gate = repairUsesGate();
  el('repair-prompt-row').hidden = gate;
  el('repair-gate-note').hidden = !gate;
  if (plan.task !== 'imported') {
    document.getElementById('repair-reason').textContent = `Observed failure · ${phaseLabels[plan.phase]}. `
      + (gate ? 'Check whether parking the idle arm clears this stage.' : 'Edit how the prompt handles this stage.');
  }
  const quick = document.getElementById('repair-scope').value === 'source';
  const layouts = quick ? plan.layouts.slice(0, 1) : plan.layouts;
  document.getElementById('repair-locked').textContent = `${plan.settings.A.model} · ${plan.settings.A.actions} steps · ${gate ? '150 simulated' : plan.seconds} s each · ${layouts.map((id) => layoutLabel(id, plan.scene_name)).join(', ')}`;
  const trials = quick ? 2 : layouts.length * 6;
  const budget = Math.ceil(trials * (gate ? 200 : plan.seconds) / 60);
  document.getElementById('repair-budget').textContent = `${trials} trials · up to ${budget} min of trial time, plus setup.`;
}

window.planRepair = async (runId) => {
  const response = await fetch(`/api/repair?run_id=${encodeURIComponent(runId)}`);
  const plan = await response.json();
  if (!response.ok) throw Error(plan.error);
  showRepair(plan);
  window.openWorkspace?.('benchmark');
  el('repair-candidate').focus();
};

document.getElementById('repair-clear').onclick = () => showRepair(null);
document.getElementById('repair-scope').onchange = renderRepairScope;
el('repair-fix').onchange = renderRepairScope;
document.getElementById('repair-start').onclick = async () => {
  if (!pendingRepair || comparisonRequest) return;
  const candidate = el('repair-candidate');
  const gate = repairUsesGate();
  if (!gate && !candidate.reportValidity()) return;
  comparisonRequest = true;
  document.getElementById('repair-start').disabled = true;
  bench('status').textContent = 'Preparing the fix comparison…';
  try {
    savedBenchmark = null;
    const result = await benchmarkRequest('/api/benchmark', {repair_of: pendingRepair.source_run,
      scope: document.getElementById('repair-scope').value,
      ...(gate ? {candidate_assist: true, candidate_prompt: pendingRepair.settings.A.prompt} : {candidate_prompt: candidate.value})});
    repairReportId = result.id;
  } catch (error) {
    bench('status').textContent = error.message;
    document.getElementById('repair-start').disabled = false;
  } finally {
    comparisonRequest = false;
  }
};

bench('stop').onclick = async () => {
  if (cancellationRequested) return;
  cancellationRequested = true;
  bench('stop').disabled = true;
  try {
    await benchmarkRequest('/api/benchmark/cancel', {id: latestBenchmark.id});
  } catch (error) {
    cancellationRequested = false;
    bench('status').textContent = error.message;
  }
};
bench('follow').onclick = () => {
  if (!el('latest').hidden) el('latest').click();
};
bench('trials').onclick = (event) => {
  const button = event.target.closest('[data-inspect]');
  if (button) inspectPair(Number(button.dataset.inspect), true);
};
async function openComparison(id) {
  const request = ++reportRequest;
  try {
    if (!id || id === latestBenchmark?.id) {
      savedBenchmark = null;
    } else {
      const response = await fetch(`/api/benchmark?id=${encodeURIComponent(id)}`);
      if (!response.ok) throw Error('Could not open this comparison.');
      const report = await response.json();
      if (request !== reportRequest) return;
      savedBenchmark = report;
    }
    const url = new URL(window.location);
    if (id) url.searchParams.set('comparison', id);
    else url.searchParams.delete('comparison');
    url.hash = 'benchmark';
    history.replaceState(null, '', url);
    window.renderBenchmark(state.benchmark, state.current, state.benchmark_history);
  } catch (error) {
    if (request === reportRequest) bench('status').textContent = error.message;
  }
}
bench('archive-list').onclick = (event) => {
  const button = event.target.closest('[data-report]');
  if (button) {
    showRepair(null);
    openComparison(button.dataset.report);
  }
};
bench('refresh').onclick = () => openComparison(displayedBenchmark.id);
bench('new').onclick = async () => {
  await openComparison('');
  bench('goal').focus();
};
bench('view-final').onclick = () => { showPairPeak = false; renderPairFrames(); };
bench('view-peak').onclick = () => { showPairPeak = true; renderPairFrames(); };
bench('camera').onchange = renderPairFrames;
window.comparePrompt = (prompt, task = 'apple') => {
  showRepair(null);
  bench('variable').value = 'prompt';
  bench('goal').value = task;
  bench('a').value = prompt;
  const candidate = demoPromptFor(task);
  bench('b').value = prompt === candidate ? '' : candidate;
  toggleVariable();
  window.openWorkspace?.('benchmark');
  bench('b').focus();
};
bench('goal').onchange = () => {
  bench('a').value = defaultPrompt(bench('goal').value);
  bench('b').value = demoPromptFor(bench('goal').value);
};
bench('variable').onchange = toggleVariable;
bench('b').value = demoPrompt;
toggleVariable();
