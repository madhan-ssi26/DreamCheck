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

const benchStatusLabels = {running: 'Running', complete: 'Complete', canceled: 'Canceled', interrupted: 'Interrupted'};
const layoutLabels = {stock: 'Stock scene', workshop: 'Workshop', 'workshop-left': 'Workshop · left', 'workshop-right': 'Workshop · right', 'holdout-left': 'Holdout · left', 'holdout-right': 'Holdout · right', 'holdout-far': 'Holdout · far'};
const resultLabels = {passed: 'Passed', task_timeout: 'Timed out', api_error: 'API error', sim_error: 'Simulator error', canceled: 'Stopped'};
const phaseLabels = {pickup: 'Pickup', transport: 'Transport', release: 'Release', completed: 'Completed'};

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
  const rate = run.executed_steps ? `${run.clipped_steps}/${run.executed_steps} clipped` : '';
  const lift = Math.min(100, run.peak_lift_mm / 1.5);
  return `<td class="bench-cell" data-verdict="${escapeText(run.verdict)}">
    <a href="/?run=${encodeURIComponent(run.run_id)}#judge" title="Open ${slot} simulator evidence">${escapeText(resultLabels[run.verdict] ?? run.verdict)}</a>
    <small>${escapeText(run.error || run.diagnosis || run.milestone)}</small>
    ${run.failure_phase ? `<small>Stage · ${phaseLabels[run.failure_phase]}</small>` : ''}
    <small>${run.peak_lift_mm} mm peak lift${rate ? ` · ${rate}` : ''}</small>
    <div class="bench-lift" aria-hidden="true"><i style="width:${lift}%"></i></div></td>`;
}

function configLabel(benchmark, slot) {
  const setting = benchmark.settings[slot];
  if (benchmark.variable === 'prompt') return `“${escapeText(setting.prompt)}”`;
  if (benchmark.variable === 'cadence') return `${setting.actions} steps per response`;
  return `${escapeText(setting.model)} · ${setting.actions} steps per response`;
}

window.renderBenchmark = (latest, current, archive = []) => {
  latestBenchmark = latest;
  if (latest && requestedComparison) {
    const id = requestedComparison;
    requestedComparison = null;
    if (id !== latest.id) openComparison(id);
  }
  const benchmark = savedBenchmark ?? latest;
  if (displayedBenchmark?.id !== benchmark?.id) {
    cancellationRequested = false;
    inspectedPair = null;
    bench('inspector').hidden = true;
  }
  displayedBenchmark = benchmark;
  const running = benchmark?.status === 'running';
  const working = latest?.status === 'running';
  bench('start').disabled = Boolean(working || current || comparisonRequest);
  document.getElementById('repair-start').disabled = Boolean(working || current || comparisonRequest);
  bench('stop').hidden = !working;
  bench('running-actions').hidden = !working;
  bench('stop').disabled = cancellationRequested;
  bench('follow').hidden = !working;
  bench('archive').hidden = archive.length < 2;
  const savedKey = `${savedBenchmark?.id}/${archive.map((item) => `${item.id}/${item.status}`).join(',')}`;
  if (archiveKey !== savedKey) {
    archiveKey = savedKey;
    const names = {prompt: 'Prompt', cadence: 'Step limit', checkpoint: 'Checkpoint'};
    bench('archive-list').innerHTML = [{id: '', label: 'Latest comparison'}, ...archive.filter((item) => item.id !== latest?.id)
      .map((item) => ({id: item.id, label: `${names[item.variable]} · ${benchStatusLabels[item.status]} · ${new Date(item.started_at).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}`}))]
      .map((item) => `<button class="secondary" data-report="${item.id}" aria-pressed="${item.id === (savedBenchmark?.id ?? '')}">${escapeText(item.label)}</button>`).join('');
  }
  bench('label').textContent = benchmark ? benchStatusLabels[benchmark.status] ?? benchmark.status : '';
  bench('empty').hidden = Boolean(benchmark);
  if (!benchmark) return;
  const finished = benchmark.pairs.reduce((sum, pair) => sum + Object.keys(pair.runs).length, 0);
  const total = benchmark.pairs.length * 2;
  if (!comparisonRequest) {
    const active = benchmark.active;
    const location = active ? `${layoutLabels[active.layout]} · ${active.slot} · repeat ${active.repeat}` : '';
    bench('status').textContent = benchmark.error || (cancellationRequested && running
      ? 'Stopping after the current request returns…' : `${finished}/${total} trials finished${location ? ` · ${location}` : ''}`);
  }
  const key = `${benchmark.id}/${benchmark.status}/${finished}/${benchmark.active?.run_id}`;
  if (key === benchmarkKey) return;
  benchmarkKey = key;
  const {summary} = benchmark;
  const delta = summary.B.passes - summary.A.passes;
  const conclusion = !summary.matched ? 'Waiting for completed pairs' :
    delta === 0 ? 'Same completion count' : `Candidate ${delta > 0 ? 'gained' : 'lost'} ${Math.abs(delta)} ${Math.abs(delta) === 1 ? 'pass' : 'passes'}`;
  const stageRows = Object.entries(phaseLabels).map(([key, label]) => `<tr><th scope="row">${label}</th>
    <td>${summary.A.phases?.[key] ?? '—'}</td><td>${summary.B.phases?.[key] ?? '—'}</td></tr>`).join('');
  const liftSummary = summary.matched
    ? `Mean peak lift across matched pairs · A ${summary.A.mean_peak_lift_mm} mm · B ${summary.B.mean_peak_lift_mm} mm`
    : 'Mean peak lift appears once both settings finish a scene.';
  const rows = benchmark.pairs.map((pair, index) => `<tr><th scope="row">${layoutLabels[pair.layout] ?? pair.layout}<br>
    <small>Repeat ${pair.repeat}</small> ${canInspect(pair) ? `<button class="text-button" data-inspect="${index}">Compare frames</button>` : ''}
    </th>${resultCell(pair, 'A', benchmark.active, running)}
    ${resultCell(pair, 'B', benchmark.active, running)}</tr>`).join('');
  bench('report').innerHTML = `
    <div class="section-heading"><h2>Comparison results</h2><div class="action-setting">
    <a href="/api/benchmark?id=${benchmark.id}&format=csv" download>Download CSV</a>
    <a href="/api/benchmark?id=${benchmark.id}" download>JSON</a></div></div>
    <div class="bench-scoreboard">
      <div class="bench-score"><strong>${summary.A.passes}/${summary.matched}</strong> Baseline passes</div>
      <div class="bench-score"><strong>${summary.B.passes}/${summary.matched}</strong> Candidate passes</div>
      <div class="bench-score"><strong>${summary.matched}/${benchmark.pairs.length}</strong> Matched pairs</div>
    </div>
    ${benchmark.repair ? `<p><strong>${phaseLabels[benchmark.repair.phase]} fix · ${conclusion}</strong> · <a href="/?run=${benchmark.repair.source_run}#judge">Original failure</a></p>` : ''}
    <p class="muted">${liftSummary}</p>
    <p class="bench-configs"><strong>A</strong> ${configLabel(benchmark, 'A')}<br><strong>B</strong> ${configLabel(benchmark, 'B')}</p>
    <p class="muted">${escapeText(benchmark.task ?? 'apple')} into bowl · ${benchmark.seconds} s per trial. Pass rates include pairs with two valid verdicts.</p>
    ${summary.A.phases ? `<details class="setup-options" ${benchmark.repair ? 'open' : ''}><summary>Where attempts stopped</summary>
      <table class="phase-counts"><thead><tr><th>Stage</th><th>A · Baseline</th><th>B · Candidate</th></tr></thead><tbody>${stageRows}</tbody></table></details>` : ''}
    <div class="table-scroll"><table><thead><tr><th>Starting scene</th><th>A&nbsp;·&nbsp;Baseline</th><th>B&nbsp;·&nbsp;Candidate</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  bench('report').hidden = false;
  if (!inspectedPair) {
    const first = benchmark.pairs.findIndex(canInspect);
    if (first !== -1) inspectPair(first);
  }
};

function canInspect(pair) {
  return ['A', 'B'].every((slot) => ['passed', 'task_timeout'].includes(pair.runs[slot]?.verdict));
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
      const peak = details.run.chunks.reduce((best, chunk) => !best || chunk.peak_lift_mm > best.peak_lift_mm ? chunk : best, null);
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
  bench('inspector-title').textContent = `${layoutLabels[inspectedPair.layout]} · repeat ${inspectedPair.repeat}`;
  bench('view-final').setAttribute('aria-pressed', !showPairPeak);
  bench('view-peak').setAttribute('aria-pressed', showPairPeak);
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
      <figcaption><strong>${slot} · ${label}</strong> · ${run.peak_lift_mm} mm peak lift ·
      <a href="/?run=${encodeURIComponent(run.run_id)}#judge">Open action timeline</a></figcaption></figure>`;
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
  document.getElementById('repair-setup').hidden = !plan;
  bench('form').hidden = Boolean(plan);
  if (!plan) return;
  document.getElementById('repair-reason').textContent = `${phaseLabels[plan.phase]} is the last incomplete stage. Try one added instruction.`;
  document.getElementById('repair-original').textContent = plan.settings.A.prompt;
  document.getElementById('repair-instruction').textContent = plan.instruction;
  document.getElementById('repair-scope').value = 'suite';
  renderRepairScope();
}

function renderRepairScope() {
  if (!pendingRepair) return;
  const plan = pendingRepair;
  const quick = document.getElementById('repair-scope').value === 'source';
  const layouts = quick ? plan.layouts.slice(0, 1) : plan.layouts;
  document.getElementById('repair-locked').textContent = `${plan.settings.A.model} · ${plan.settings.A.actions} steps · ${plan.seconds} s each · ${layouts.map((layout) => layoutLabels[layout] ?? layout).join(', ')}`;
  document.getElementById('repair-budget').textContent = quick ? '2 trials on the original scene.' : '18 trials, with the order alternating across pairs.';
}

window.planRepair = async (runId) => {
  const response = await fetch(`/api/repair?run_id=${encodeURIComponent(runId)}`);
  const plan = await response.json();
  if (!response.ok) throw Error(plan.error);
  showRepair(plan);
  window.openWorkspace?.('benchmark');
  document.getElementById('repair-scope').focus();
};

document.getElementById('repair-clear').onclick = () => showRepair(null);
document.getElementById('repair-scope').onchange = renderRepairScope;
document.getElementById('repair-start').onclick = async () => {
  if (!pendingRepair || comparisonRequest) return;
  comparisonRequest = true;
  document.getElementById('repair-start').disabled = true;
  bench('status').textContent = 'Preparing the fix comparison…';
  try {
    savedBenchmark = null;
    await benchmarkRequest('/api/benchmark', {repair_of: pendingRepair.source_run, scope: document.getElementById('repair-scope').value});
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
bench('report').onclick = (event) => {
  const button = event.target.closest('[data-inspect]');
  if (button) inspectPair(Number(button.dataset.inspect), true);
};
async function openComparison(id) {
  try {
    if (!id || id === latestBenchmark?.id) {
      savedBenchmark = null;
    } else {
      const response = await fetch(`/api/benchmark?id=${encodeURIComponent(id)}`);
      if (!response.ok) throw Error('Could not open this comparison.');
      savedBenchmark = await response.json();
    }
    const url = new URL(window.location);
    if (id) url.searchParams.set('comparison', id);
    else url.searchParams.delete('comparison');
    url.hash = 'benchmark';
    history.replaceState(null, '', url);
    window.renderBenchmark(state.benchmark, state.current, state.benchmark_history);
  } catch (error) {
    bench('status').textContent = error.message;
  }
}
bench('archive-list').onclick = (event) => {
  const button = event.target.closest('[data-report]');
  if (button) openComparison(button.dataset.report);
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
