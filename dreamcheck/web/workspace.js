const navigation = [...document.querySelectorAll('[data-workspace]')];
const panels = [...document.querySelectorAll('[data-view-panel]')];
const defaultPanel = new URLSearchParams(location.search).has('comparison') &&
  !new URLSearchParams(location.search).has('run') ? 'benchmark'
  : new URLSearchParams(location.search).has('run') ? 'judge' : 'home';
let visiblePanel = null;

function showWorkspace(name) {
  const workspace = name === 'recording' ? 'recordings'
    : panels.some((panel) => panel.id === name) ? name : defaultPanel;
  const changed = visiblePanel !== workspace;
  visiblePanel = workspace;
  for (const panel of panels) panel.hidden = panel.id !== workspace;
  for (const link of navigation) {
    if (link.dataset.workspace === workspace) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  const panel = document.getElementById(workspace);
  if (workspace === 'recordings') window.showImportType(name === 'recording' ? 'recording' : 'scene');
  document.title = `DreamCheck · ${panel.querySelector('h1').textContent}`;
  if (changed) window.scrollTo(0, 0);
}

window.openWorkspace = (name) => {
  const url = new URL(location.href);
  url.hash = name;
  if (location.hash !== url.hash) history.pushState(null, '', url);
  showWorkspace(name);
};

for (const link of navigation) {
  link.addEventListener('click', (event) => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    window.openWorkspace(link.dataset.workspace);
  });
}

window.addEventListener('hashchange', () => showWorkspace(location.hash.slice(1) || defaultPanel));
window.addEventListener('popstate', () => showWorkspace(location.hash.slice(1) || defaultPanel));
showWorkspace(location.hash.slice(1) || defaultPanel);

for (const button of document.querySelectorAll('[data-jump]')) {
  button.onclick = () => document.getElementById(button.dataset.jump).scrollIntoView({block: 'start'});
}

for (const name of ['timeline', 'output']) {
  document.getElementById(`show-${name}`).onclick = () => {
    document.getElementById('timeline-pane').hidden = name !== 'timeline';
    document.getElementById('output-pane').hidden = name !== 'output';
    document.getElementById('show-timeline').setAttribute('aria-pressed', name === 'timeline');
    document.getElementById('show-output').setAttribute('aria-pressed', name === 'output');
  };
}
