const stages = [...document.querySelectorAll('[data-frame]')];
const image = document.getElementById('replay-image');
const description = document.getElementById('replay-description');

for (const [index, stage] of stages.entries()) {
  stage.addEventListener('click', () => {
    for (const button of stages) button.setAttribute('aria-pressed', String(button === stage));
    image.src = `./${stage.dataset.frame}.webp`;
    image.alt = description.textContent = stage.dataset.description;
  });
  stage.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const direction = event.key === 'ArrowRight' ? 1 : -1;
    const next = stages[(index + direction + stages.length) % stages.length];
    next.focus();
    next.click();
  });
}
