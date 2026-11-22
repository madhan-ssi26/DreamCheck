(() => {
  const el = (id) => document.getElementById(id);
  const status = el('scene-upload-status');
  const picker = el('saved-scenes');
  const editor = el('scene-goal');
  const score = el('scene-score');
  const dimensions = ['width', 'depth', 'height'].map((name) => el(`scene-${name}`));
  const dwell = el('scene-dwell');
  let scenes = [];
  let request = 0;

  function readGoal() {
    if (editor.hidden || !score.checked) return null;
    if ([...dimensions, dwell].some((input) => !input.validity.valid)) return null;
    return {object: el('scene-object').value, destination: el('scene-destination').value,
      size_cm: dimensions.map((input) => input.valueAsNumber), dwell_ticks: dwell.valueAsNumber};
  }

  function previewGoal() {
    const goal = readGoal();
    const footprint = el('scene-footprint');
    footprint.hidden = !goal;
    el('scene-use').disabled = !picker.value || (score.checked && !goal);
    if (!goal) return;
    const [width, depth, height] = goal.size_cm;
    const box = footprint.querySelector('div');
    const scale = 150 / Math.max(width, depth);
    box.style.width = `${width * scale}px`;
    box.style.height = `${depth * scale}px`;
    const destination = goal.destination.slice(goal.destination.indexOf(':') + 1);
    footprint.querySelector('figcaption').textContent = `Top-down footprint centered at ${destination} · ${width} × ${depth} cm · ${height} cm tall`;
    el('scene-goal-summary').textContent = `Require ${goal.object} inside for ${(goal.dwell_ticks * .05).toFixed(2)} s.`;
  }

  editor.addEventListener('toggle', previewGoal);
  editor.addEventListener('input', previewGoal);
  score.onchange = () => {
    editor.hidden = !score.checked;
    editor.open = score.checked;
    previewGoal();
  };

  function selectScene() {
    const scene = scenes.find((item) => item.id === picker.value);
    editor.open = false;
    editor.hidden = true;
    score.checked = false;
    el('scene-score-option').hidden = !(scene?.objects?.length && scene?.destinations?.length);
    el('scene-object').replaceChildren(...(scene?.objects ?? []).map((name) => new Option(name)));
    el('scene-destination').replaceChildren(...(scene?.destinations ?? []).map((name) => new Option(name.replace(':', ' · '), name)));
    previewGoal();
    el('scene-preview').hidden = !scene?.preview;
    el('scene-preview-empty').hidden = Boolean(scene?.preview);
    if (scene?.preview) el('scene-preview').src = `/api/scene-preview?id=${encodeURIComponent(scene.id)}`;
    else el('scene-preview').removeAttribute('src');
    el('scene-preview-empty').textContent = scene ? 'This scene is ready. Open it in Judge to see its cameras.' : 'Select a scene to inspect its starting view.';
  }

  function showLibrary(selected) {
    picker.replaceChildren(new Option('Choose a scene', ''), ...scenes.map((scene) => new Option(scene.name, scene.id)));
    for (const scene of scenes) window.addSceneOption(scene.id, scene.name);
    picker.value = selected ?? '';
    selectScene();
  }

  async function loadLibrary() {
    try {
      const response = await fetch('/api/scenes');
      if (!response.ok) throw Error('Could not load saved scenes.');
      const saved = await response.json();
      scenes = [...new Map([...saved, ...scenes].map((scene) => [scene.id, scene])).values()];
      showLibrary(picker.value);
    } catch (error) {
      status.textContent = error.message;
    }
  }

  picker.onchange = selectScene;
  el('scene-use').onclick = () => {
    const scene = scenes.find((item) => item.id === picker.value);
    if (scene) window.useImportedScene(scene.id, scene.name, readGoal());
  };
  el('scene-package').onchange = async (event) => {
    const token = ++request;
    const file = event.target.files[0];
    if (!file) return;
    status.textContent = 'Checking the scene, servos, and cameras…';
    try {
      const response = await fetch('/api/import-scene', {method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: file});
      const result = await response.json();
      if (token !== request) return;
      if (!response.ok) throw Error(result.error);
      scenes.unshift(result);
      showLibrary(result.id);
      status.textContent = `${result.name} is ready to run.`;
    } catch (error) {
      if (token === request) status.textContent = error.message;
    }
  };
  window.showImportType = (mode) => {
    for (const name of ['scene', 'recording']) {
      el(`${name}-import-panel`).hidden = mode !== name;
      el(`choose-${name}-import`).setAttribute('aria-pressed', mode === name);
    }
  };
  for (const mode of ['scene', 'recording']) {
    el(`choose-${mode}-import`).onclick = () => window.openWorkspace(mode === 'scene' ? 'recordings' : 'recording');
  }
  loadLibrary();
})();
