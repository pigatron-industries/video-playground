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
const SEG_TOP = 112;  // top of the video segment row (below the keyframe track)
const SEG_H = 46;     // height of a segment block

// Render status → block colors. Status lives on the *segment* (it comes
// from the saved plan and the render job), not on the keyframe.
const SEG_COLORS = {
  empty:     { fill: 'rgba(91,140,255,0.10)', stroke: 'rgba(91,140,255,0.45)', text: '#8b8b95' },
  queued:    { fill: 'rgba(224,179,65,0.15)', stroke: 'rgba(224,179,65,0.60)', text: '#e0b341' },
  rendering: { fill: 'rgba(224,179,65,0.30)', stroke: 'rgba(224,179,65,0.90)', text: '#e0b341' },
  done:      { fill: 'rgba(63,185,80,0.18)',  stroke: 'rgba(63,185,80,0.70)',  text: '#3fb950' },
  error:     { fill: 'rgba(248,81,73,0.16)',  stroke: 'rgba(248,81,73,0.70)',  text: '#f85149' },
};

let totalDuration = 60;
let keyframes = [];
let selectedId = null;      // selected keyframe dot (top track)
let selectedSegId = null;   // selected segment block (bottom row)
let segStatus = {};         // segment id → render status ('empty'|'queued'|'rendering'|'done'|'error')
let dragId = null;
let dragOffsetX = 0;
let dragMoved = false;
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
window.setSegmentStatus = () => {};
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
  canvas.height = 200;
  draw();
}

// Segments are derived state: the gap between each pair of consecutive
// keyframes (sorted by time). IDs match what exportPlan persists
// ("kfA-kfB") so saved render status maps onto the live blocks.
function getSegments() {
  const sorted = [...keyframes].sort((a, b) => a.time - b.time);
  const segs = [];
  for (let i = 0; i < sorted.length - 1; i++) {
    segs.push({
      id: `${sorted[i].id}-${sorted[i + 1].id}`,
      start: sorted[i].time,
      end: sorted[i + 1].time,
      prompt: sorted[i + 1].prompt || '',
    });
  }
  return segs;
}

function roundRect(x, y, w, h, r) {
  r = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
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

  drawVideoRow();
}

function drawVideoRow() {
  ctx.font = '10px -apple-system, sans-serif';
  ctx.fillStyle = '#8b8b95';
  ctx.fillText('KEYFRAMES', 8, TRACK_Y - 16);
  ctx.fillText('VIDEO', 8, SEG_TOP + 14);

  // Faint full-length rail behind the segment blocks.
  ctx.strokeStyle = '#2a2a30';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(40, SEG_TOP + SEG_H / 2);
  ctx.lineTo(timeToX(totalDuration), SEG_TOP + SEG_H / 2);
  ctx.stroke();

  const segs = getSegments();
  if (segs.length === 0) {
    ctx.save();
    ctx.strokeStyle = '#34343c';
    ctx.setLineDash([4, 4]);
    ctx.lineWidth = 1;
    ctx.strokeRect(40, SEG_TOP, timeToX(totalDuration) - 40, SEG_H);
    ctx.restore();
    ctx.fillStyle = '#55555e';
    ctx.fillText('Add two or more keyframes to create a video segment', 52, SEG_TOP + SEG_H / 2 + 3);
    return;
  }

  for (const s of segs) {
    const x1 = timeToX(s.start) + 2;
    const w = Math.max(2, timeToX(s.end) - x1 - 2);
    const status = segStatus[s.id] || 'empty';
    const colors = SEG_COLORS[status] || SEG_COLORS.empty;
    const selected = s.id === selectedSegId;

    roundRect(x1, SEG_TOP, w, SEG_H, 6);
    ctx.fillStyle = colors.fill;
    ctx.fill();
    ctx.lineWidth = selected ? 2 : 1;
    ctx.strokeStyle = selected ? '#e6e6ea' : colors.stroke;
    ctx.stroke();

    if (w >= 36) {
      const cx = x1 + w / 2;
      const cy = SEG_TOP + SEG_H / 2;
      ctx.textAlign = 'center';
      if (w >= 90 && status !== 'empty') {
        ctx.fillStyle = '#c9c9d2';
        ctx.fillText((s.end - s.start).toFixed(1) + 's', cx, cy - 2);
        ctx.fillStyle = colors.text;
        ctx.fillText(status, cx, cy + 10);
      } else {
        ctx.fillStyle = '#c9c9d2';
        ctx.fillText((s.end - s.start).toFixed(1) + 's', cx, cy + 3);
      }
      ctx.textAlign = 'start';
    }
  }
}

function findSegAt(x, y) {
  if (y < SEG_TOP || y > SEG_TOP + SEG_H) return null;
  for (const s of getSegments()) {
    if (x >= timeToX(s.start) && x <= timeToX(s.end)) return s;
  }
  return null;
}

function findKfAt(x, y) {
  for (const kf of keyframes) {
    const kx = timeToX(kf.time);
    if (Math.hypot(kx - x, TRACK_Y - y) <= KF_R + 4) return kf;
  }
  return null;
}

// Push the current selection to the backend. Exactly one of keyframe /
// segment is selected (or neither, to clear); the sidebar polls this back.
async function syncSelection() {
  const kf = selectedId ? keyframes.find(k => k.id === selectedId) : null;
  const seg = selectedSegId ? getSegments().find(s => s.id === selectedSegId) : null;
  await fetch('/api/ui/select', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      kind: seg ? 'segment' : 'keyframe',
      keyframe_id: kf ? kf.id : null,
      segment_id: seg ? seg.id : null,
      time: kf ? kf.time : 0,
      prompt: (seg || kf) ? ((seg || kf).prompt || '') : '',
      image_path: kf ? kf.imagePath : null,
      start_time: seg ? seg.start : 0,
      end_time: seg ? seg.end : 0,
      duration: seg ? Math.round((seg.end - seg.start) * 10) / 10 : 0,
      status: seg ? (segStatus[seg.id] || 'empty') : '',
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
    dragMoved = false;
    selectedId = hit.id;
    selectedSegId = null;
    syncSelection();
    draw();
  }
});

  canvas.addEventListener('mousemove', e => {
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left, y = e.clientY - rect.top;
    if (dragId !== null) {
      dragMoved = true;
      const kf = keyframes.find(k => k.id === dragId);
      if (kf) {
        kf.time = Math.round(Math.min(totalDuration, Math.max(0, xToTime(x + dragOffsetX))) * 10) / 10;
        draw(); // local only — no network call while dragging
      }
      canvas.style.cursor = 'grabbing';
      return;
    }
    canvas.style.cursor = (findKfAt(x, y) || findSegAt(x, y)) ? 'pointer' : 'crosshair';
  });

  window.addEventListener('mouseup', () => {
    if (dragId !== null) {
      syncSelection(); // sync final position once the drag settles
    }
    dragId = null;
    canvas.style.cursor = 'crosshair';
  });

  canvas.addEventListener('click', e => {
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left, y = e.clientY - rect.top;

    // A completed drag ends with a click on the canvas too — ignore it so
    // releasing over a segment block doesn't accidentally select it.
    if (dragMoved) { dragMoved = false; return; }

    const kfHit = findKfAt(x, y);
    if (kfHit) {
      selectedId = kfHit.id;
      selectedSegId = null;
      syncSelection();
      draw();
      return;
    }

    const segHit = findSegAt(x, y);
    if (segHit) {
      selectedSegId = segHit.id;
      selectedId = null;
      syncSelection();
      draw();
      return;
    }

    if (Math.abs(y - TRACK_Y) < 20) {
      const kf = { id: 'kf' + nextId++, time: Math.round(xToTime(x) * 10) / 10, prompt: '', imagePath: null };
      keyframes.push(kf);
      selectedId = kf.id;
      selectedSegId = null;
      resizeCanvas();
      syncSelection();
      return;
    }

    // Click on empty space clears any selection.
    if (selectedId !== null || selectedSegId !== null) {
      selectedId = null;
      selectedSegId = null;
      syncSelection();
      draw();
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
    selectedSegId = null; // segments are derived — any stale id is invalid now
    resizeCanvas();
    syncSelection();
  };

  window.setSegmentPreview = (url) => {
    const v = document.getElementById('segPreview');
    if (v) {
      v.src = url;
      v.load();
    }
  };

  window.setSegmentStatus = (id, status) => {
    segStatus[id] = status;
    draw();
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
    selectedSegId = null;
    // Restore each segment's render status onto its block.
    segStatus = {};
    (plan.segments || []).forEach(s => { segStatus[s.id] = s.status || 'empty'; });
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
