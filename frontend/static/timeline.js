// Canvas timeline: drag/click interactions stay entirely client-side for
// responsiveness. Only "settled" events (selection changes, keyframe adds,
// drag-end) talk to the backend — continuous mousemove never does.
//
// NiceGUI injects the #canvasWrap/#timeline markup asynchronously (over
// its websocket, not as part of the initial page HTML), so this script
// can run before those elements exist. Everything below waits for both
// to actually be in the DOM before touching them, instead of assuming
// they're already there.

const PPS = 40;
const TRACK_Y = 60;
const KF_R = 10;

let totalDuration = 60;
let keyframes = [];
let selectedId = null;
let dragId = null;
let dragOffsetX = 0;
let nextId = 1;

let canvas, ctx, wrap;
let initialized = false;

// Stub the window.* hooks immediately so an early call from the Python
// side (e.g. the total-duration field changing before init finishes)
// doesn't throw "not a function" — real implementations overwrite these
// once init() runs. loadPlan queues its argument so a page-refresh restore
// arriving before init still works.
let _pendingPlan = null;
window.setTotalDuration = (v) => { totalDuration = v; };
window.setKeyframePrompt = () => {};
window.setKeyframeTime = () => {};
window.setKeyframeImage = () => {};
window.deleteKeyframe = () => {};
window.exportPlan = () => {};
window.setSegmentPreview = () => {};
window.loadPlan = (plan) => { _pendingPlan = plan; };

function waitForElements(cb) {
  const c = document.getElementById('timeline');
  const w = document.getElementById('canvasWrap');
  if (c && w) return cb(c, w);
  requestAnimationFrame(() => waitForElements(cb));
}

waitForElements((c, w) => {
  canvas = c;
  ctx = canvas.getContext('2d');
  wrap = w;
  init();
});

function timeToX(t) { return 40 + t * PPS; }
function xToTime(x) { return Math.max(0, (x - 40) / PPS); }

function resizeCanvas() {
  canvas.width = Math.max(wrap.clientWidth, totalDuration * PPS + 80);
  canvas.height = 320;
  draw();
}

function draw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = '#34343c';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(40, TRACK_Y);
  ctx.lineTo(timeToX(totalDuration), TRACK_Y);
  ctx.stroke();

  ctx.fillStyle = '#8b8b95';
  ctx.font = '10px -apple-system, sans-serif';
  for (let t = 0; t <= totalDuration; t += 5) {
    const x = timeToX(t);
    ctx.beginPath();
    ctx.moveTo(x, TRACK_Y - 4);
    ctx.lineTo(x, TRACK_Y + 4);
    ctx.strokeStyle = '#34343c';
    ctx.stroke();
    ctx.fillText(t + 's', x - 8, TRACK_Y + 24);
  }

  const sorted = [...keyframes].sort((a, b) => a.time - b.time);
  ctx.strokeStyle = '#5b8cff55';
  ctx.lineWidth = 4;
  for (let i = 0; i < sorted.length - 1; i++) {
    ctx.beginPath();
    ctx.moveTo(timeToX(sorted[i].time), TRACK_Y);
    ctx.lineTo(timeToX(sorted[i + 1].time), TRACK_Y);
    ctx.stroke();
  }

  keyframes.forEach(kf => {
    const x = timeToX(kf.time);
    ctx.beginPath();
    ctx.arc(x, TRACK_Y, KF_R, 0, Math.PI * 2);
    ctx.fillStyle = kf.id === selectedId ? '#5b8cff' : (kf.imagePath ? '#e6e6ea' : '#48484f');
    ctx.fill();
    ctx.strokeStyle = '#1a1a1e';
    ctx.lineWidth = 2;
    ctx.stroke();
    if (kf.prompt) {
      ctx.fillStyle = '#5b8cff';
      ctx.beginPath();
      ctx.arc(x, TRACK_Y - 18, 3, 0, Math.PI * 2);
      ctx.fill();
    }
  });
}

function findKfAt(x, y) {
  for (const kf of keyframes) {
    const kx = timeToX(kf.time);
    if (Math.hypot(kx - x, TRACK_Y - y) <= KF_R + 4) return kf;
  }
  return null;
}

async function postSelection(kf) {
  await fetch('/api/ui/select', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      keyframe_id: kf ? kf.id : null,
      time: kf ? kf.time : 0,
      prompt: kf ? kf.prompt : '',
      image_path: kf ? kf.imagePath : null,
    }),
  });
}

function init() {
  initialized = true;

  canvas.addEventListener('mousedown', e => {
  const rect = canvas.getBoundingClientRect();
  const x = e.clientX - rect.left, y = e.clientY - rect.top;
  const hit = findKfAt(x, y);
  if (hit) {
    dragId = hit.id;
    dragOffsetX = timeToX(hit.time) - x;
    selectedId = hit.id;
    postSelection(hit);
    draw();
  }
});

  canvas.addEventListener('mousemove', e => {
    if (dragId === null) return;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left + dragOffsetX;
    const kf = keyframes.find(k => k.id === dragId);
    if (kf) {
      kf.time = Math.round(Math.min(totalDuration, Math.max(0, xToTime(x))) * 10) / 10;
      draw(); // local only — no network call while dragging
    }
  });

  window.addEventListener('mouseup', () => {
    if (dragId !== null) {
      const kf = keyframes.find(k => k.id === dragId);
      if (kf) postSelection(kf); // sync final position once the drag settles
    }
    dragId = null;
  });

  canvas.addEventListener('click', e => {
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left, y = e.clientY - rect.top;
    const hit = findKfAt(x, y);
    if (hit) {
      selectedId = hit.id;
      postSelection(hit);
      draw();
    } else if (Math.abs(y - TRACK_Y) < 20) {
      const kf = { id: 'kf' + nextId++, time: Math.round(xToTime(x) * 10) / 10, prompt: '', imagePath: null };
      keyframes.push(kf);
      selectedId = kf.id;
      resizeCanvas();
      postSelection(kf);
    }
  });

  // --- Functions called FROM Python (via ui.run_javascript) ---------------
  // Overwrite the startup stubs now that the canvas is actually live.

  window.setTotalDuration = (v) => { totalDuration = v; resizeCanvas(); };

  window.setKeyframePrompt = (id, text) => {
    const kf = keyframes.find(k => k.id === id);
    if (kf) { kf.prompt = text; draw(); }
  };

  window.setKeyframeTime = (id, t) => {
    const kf = keyframes.find(k => k.id === id);
    if (kf) { kf.time = t; draw(); }
  };

  window.setKeyframeImage = (id, path) => {
    const kf = keyframes.find(k => k.id === id);
    if (kf) { kf.imagePath = path; draw(); }
  };

  window.deleteKeyframe = (id) => {
    keyframes = keyframes.filter(k => k.id !== id);
    if (selectedId === id) selectedId = null;
    resizeCanvas();
  };

  window.setSegmentPreview = (url) => {
    const v = document.getElementById('segPreview');
    if (v) {
      v.src = url;
      v.load();
    }
  };

  window.exportPlan = () => {
    const sorted = [...keyframes].sort((a, b) => a.time - b.time);
    const segments = [];
    for (let i = 0; i < sorted.length - 1; i++) {
      segments.push({
        id: `${sorted[i].id}-${sorted[i + 1].id}`,
        start_time: sorted[i].time,
        end_time: sorted[i + 1].time,
        duration: Math.round((sorted[i + 1].time - sorted[i].time) * 10) / 10,
        start_image_path: sorted[i].imagePath,
        end_image_path: sorted[i + 1].imagePath,
        prompt: sorted[i + 1].prompt || '',
      });
    }
    const plan = {
      total_duration: totalDuration,
      keyframes: sorted.map(k => ({ id: k.id, time: k.time, image_path: k.imagePath })),
      segments,
    };
    return fetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(plan),
    }).then(r => r.json());
  };

  window.loadPlan = (plan) => {
    totalDuration = plan.total_duration || 60;
    // Rebuild keyframes. A keyframe's prompt is persisted on the segment that
    // ends at it (see exportPlan above), so map that back onto the keyframe.
    // Strip any legacy /api/projects/images/ prefix so we always store just the filename.
    const kfs = (plan.keyframes || []).map(k => ({
      id: k.id, time: k.time, prompt: '',
      imagePath: k.image_path ? k.image_path.replace(/^\/api\/projects\/images\//, '') : null,
    }));
    const segs = (plan.segments || []).slice().sort((a, b) => a.start_time - b.start_time);
    segs.forEach((s, i) => { if (kfs[i + 1]) kfs[i + 1].prompt = s.prompt || ''; });
    keyframes = kfs;
    selectedId = null;
    resizeCanvas();
  };

  // Process any plan that was queued via the stub before init() ran.
  if (_pendingPlan) {
    window.loadPlan(_pendingPlan);
    _pendingPlan = null;
  }

  window.addEventListener('resize', resizeCanvas);
  resizeCanvas();
}
