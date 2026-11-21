(() => {
  const field = (id) => document.getElementById(`record-${id}`);
  const video = field('video');
  let files = [];
  let episodes = [];
  let cameras = [];
  let analysis = null;
  let media = null;
  let generation = 0;
  let bookmarkKey = '';
  let bookmarks = [];
  let pending = null;
  let metadata = {};
  const path = (file) => file.webkitRelativePath;

  function options(select, values) {
    select.replaceChildren(...values.map((value, index) => new Option(value, index)));
  }

  function setCamera(time = 0) {
    if (media) URL.revokeObjectURL(media);
    media = null;
    video.removeAttribute('src');
    const file = cameras[Number(field('camera').value)];
    field('video-panel').hidden = !file;
    field('mark').disabled = true;
    if (file) {
      media = URL.createObjectURL(file);
      video.src = media;
      video.onloadedmetadata = () => { video.currentTime = Math.min(time, video.duration); };
    }
    video.load();
  }

  function renderBookmarks() {
    field('marks').replaceChildren(...bookmarks.map((mark) => {
      const row = document.createElement('li');
      const label = document.createElement('button');
      label.className = 'text-button';
      label.textContent = `${mark.time.toFixed(2)} s · ${mark.label}`;
      label.onclick = () => {
        const camera = cameras.findIndex((file) => path(file) === mark.camera);
        if (camera < 0) {
          field('mark-status').textContent = 'The bookmarked camera file is missing.';
          return;
        }
        video.pause();
        field('camera').value = camera;
        setCamera(mark.time);
      };
      const download = document.createElement('button');
      download.className = 'secondary';
      download.textContent = 'Export evidence';
      download.onclick = async () => {
        const token = generation;
        download.disabled = true;
        try {
          const file = cameras.find((asset) => path(asset) === mark.camera);
          if (!file) throw Error('The bookmarked camera file is missing.');
          const nearest = [...analysis.windows].sort((a, b) => Math.abs(a.time - mark.time) - Math.abs(b.time - mark.time)).slice(0, 3);
          const evidence = {label: mark.label, time_seconds: mark.time, episode: path(episodes[Number(field('episode').value)]),
            camera: mark.camera, prompt: metadata.prompt ?? metadata.task ?? null, checkpoint: metadata.checkpoint ?? null,
            telemetry: nearest.sort((a, b) => a.time - b.time)};
          evidence.frame_jpeg = await captureFrame(file, mark.time);
          if (token !== generation) return;
          const url = URL.createObjectURL(new Blob([JSON.stringify(evidence, null, 2)], {type: 'application/json'}));
          const link = document.createElement('a');
          link.href = url;
          link.download = `dreamcheck-evidence-${mark.time.toFixed(2)}s.json`;
          link.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) {
          if (token === generation) field('mark-status').textContent = error.message;
        } finally {
          download.disabled = false;
        }
      };
      row.append(label, download);
      return row;
    }));
  }

  function captureFrame(file, time) {
    const source = document.createElement('video');
    source.muted = true;
    const url = URL.createObjectURL(file);
    let timeout;
    return new Promise((resolve, reject) => {
      timeout = setTimeout(() => reject(Error('The bookmarked frame took too long to decode.')), 10000);
      const draw = () => {
        try {
          const canvas = document.createElement('canvas');
          canvas.width = source.videoWidth;
          canvas.height = source.videoHeight;
          canvas.getContext('2d').drawImage(source, 0, 0);
          resolve(canvas.toDataURL('image/jpeg', .9));
        } catch (error) { reject(error); }
      };
      source.onerror = () => reject(Error('The bookmarked camera frame could not be decoded.'));
      source.onloadeddata = () => {
        const target = Math.min(time, Math.max(0, source.duration - .001));
        if (target === 0) draw();
        else source.currentTime = target;
      };
      source.onseeked = draw;
      source.src = url;
      source.load();
    }).finally(() => {
      clearTimeout(timeout);
      source.removeAttribute('src');
      source.load();
      URL.revokeObjectURL(url);
    });
  }

  function selectPoint(index, seek) {
    const point = analysis?.windows[index];
    if (!point) return;
    for (const button of field('points').children) button.setAttribute('aria-pressed', Number(button.dataset.point) === index);
    const chunk = point.chunk == null ? '' : ` · Recorded chunk ${point.chunk}`;
    field('point').textContent = `${point.time.toFixed(2)} s${chunk} · Peak state change ${point.motion.toPrecision(3)}`;
    const format = (values) => values.map((value) => Number(value.toPrecision(5))).join(', ');
    field('vectors').textContent = `Observed state [${analysis.state_width}]\n${format(point.state)}\n\nRecorded action [${analysis.action_width}]\n${format(point.action)}`;
    if (seek && video.readyState >= 1) video.currentTime = Math.min(point.time, video.duration);
  }

  async function openEpisode() {
    const token = ++generation;
    const file = episodes[Number(field('episode').value)];
    analysis = null;
    metadata = {};
    pending = null;
    field('bookmarks').hidden = field('mark-form').hidden = true;
    field('mark-status').textContent = '';
    field('trace').hidden = true;
    field('summary').textContent = field('inference').textContent = '';
    cameras = [];
    setCamera();
    if (!file) return;
    bookmarkKey = `dreamcheck-bookmarks:${path(file)}:${file.size}:${file.lastModified}`;
    try { bookmarks = JSON.parse(localStorage.getItem(bookmarkKey)) ?? []; }
    catch { bookmarks = []; }
    const root = path(file).replace(/data\/chunk-[^/]+\/episode_[^/]+\.parquet$/, '');
    const stem = file.name.replace('.parquet', '');
    cameras = files.filter((asset) => path(asset).startsWith(`${root}videos/`) && asset.name === `${stem}.mp4`);
    options(field('camera'), cameras.map((camera) => path(camera).split('/').at(-2).replace(/^observation\.images\./, '')));
    setCamera();
    field('status').textContent = 'Reading episode telemetry…';
    try {
      const response = await fetch('/api/recording', {method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: file});
      const result = await response.json();
      if (token !== generation) return;
      if (!response.ok) throw Error(result.error);
      analysis = result;
      field('mark').disabled = video.readyState < 2;
      field('bookmarks').hidden = !bookmarks.length;
      renderBookmarks();
      field('trace').hidden = false;
      const max = Math.max(1e-9, ...result.windows.map((point) => point.motion));
      field('points').innerHTML = result.windows.map((point, index) => `<button class="trace-button" data-point="${index}" aria-label="Inspect ${point.time.toFixed(2)} seconds"><span class="bars"><i style="--bar:${Math.round(100 * point.motion / max)}%"></i></span>${point.time.toFixed(1)}</button>`).join('');
      selectPoint(result.windows.reduce((best, point, index) => point.motion > result.windows[best].motion ? index : best, 0), false);
      field('summary').textContent = `${result.frames} frames · ${result.duration.toFixed(1)} s · ${cameras.length} cameras`;
      field('status').textContent = cameras.length ? 'Recording ready' : 'Telemetry ready · no matching MP4 camera files';
      const info = files.find((asset) => path(asset) === `${root}rollout.json`);
      if (info && info.size <= 1024 * 1024) {
        try {
          const loaded = JSON.parse(await info.text());
          if (token !== generation) return;
          metadata = loaded ?? {};
          const details = [metadata.task ?? metadata.prompt, metadata.checkpoint, metadata.body].filter((value) => typeof value === 'string');
          if (details.length) field('summary').textContent += ` · ${details.join(' · ')}`;
        } catch { /* Older exports may contain incomplete metadata. */ }
      }
      const log = files.find((asset) => path(asset) === `${root}inference.jsonl`);
      if (log && log.size <= 8 * 1024 * 1024 && episodes.filter((entry) => path(entry).startsWith(`${root}data/`)).length === 1) {
        const lines = (await log.text()).split(/\r?\n/);
        if (token !== generation) return;
        const times = [];
        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const entry = JSON.parse(line);
            if (Number.isFinite(entry.rtt_ms) && entry.rtt_ms >= 0) times.push(entry.rtt_ms);
          } catch { /* Skip partial log lines. */ }
        }
        times.sort((a, b) => a - b);
        if (times.length) field('inference').textContent = `${times.length} timed inference requests · median RTT ${times[Math.floor((times.length - 1) / 2)].toFixed(0)} ms · p95 ${times[Math.ceil(times.length * .95) - 1].toFixed(0)} ms`;
      }
    } catch (error) {
      if (token === generation) field('status').textContent = error.message;
    }
  }

  field('folder').onchange = () => {
    files = [...field('folder').files];
    episodes = files.filter((file) => /\/data\/chunk-[^/]+\/episode_\d+\.parquet$/.test(path(file))).sort((a, b) => path(a).localeCompare(path(b)));
    options(field('episode'), episodes.map(path));
    field('episode').disabled = !episodes.length;
    field('status').textContent = episodes.length ? '' : 'No LeRobot v2.1 episode parquet found. Select the rollout or dataset folder.';
    openEpisode();
  };
  field('episode').onchange = openEpisode;
  field('camera').onchange = () => setCamera(video.currentTime);
  video.onloadeddata = () => { field('mark').disabled = !analysis; };
  field('mark').onclick = () => {
    const camera = cameras[Number(field('camera').value)];
    if (!analysis || !camera) return;
    video.pause();
    pending = {time: video.currentTime, camera: path(camera)};
    field('mark-time').textContent = `Label at ${pending.time.toFixed(2)} s`;
    field('label').value = '';
    field('bookmarks').hidden = field('mark-form').hidden = false;
    field('label').focus();
  };
  field('mark-cancel').onclick = () => {
    pending = null;
    field('mark-form').hidden = true;
    field('bookmarks').hidden = !bookmarks.length;
  };
  field('mark-form').onsubmit = (event) => {
    event.preventDefault();
    const label = field('label').value.trim();
    if (!pending || !label) return;
    bookmarks.push({...pending, label});
    try {
      localStorage.setItem(bookmarkKey, JSON.stringify(bookmarks));
      field('mark-status').textContent = 'Bookmark saved on this browser.';
    } catch {
      field('mark-status').textContent = 'Bookmark saved for this session. Browser storage is unavailable.';
    }
    field('mark-cancel').click();
    renderBookmarks();
  };
  field('points').onclick = (event) => {
    const button = event.target.closest('[data-point]');
    if (button) selectPoint(Number(button.dataset.point), true);
  };
  video.ontimeupdate = () => {
    if (!analysis) return;
    const index = analysis.windows.findIndex((point) => point.end >= video.currentTime);
    selectPoint(index < 0 ? analysis.windows.length - 1 : index, false);
  };
})();
