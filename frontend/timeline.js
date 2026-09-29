// Timeline widget for NiceGUI (loaded via `Element, component='timeline.js'`).
//
// Python owns the state. Props in: totalDuration, keyframes, segmentStatus,
// selected. Events out: 'select' ({kind, id}) and 'change' ({keyframes}).
// Continuous mousemove while dragging stays local to the widget; only settled
// actions (mouseup after a drag, adding a keyframe, clicking) emit events.

const PPS = 40;
const TRACK_Y = 60;
const KF_R = 10;
const SEG_TOP = 112;  // top of the video segment row (below the keyframe track)
const SEG_H = 46;     // height of a segment block

// Render status -> block colors. Status lives on the *segment*, not the keyframe.
const SEG_COLORS = {
  empty:     { fill: 'rgba(91,140,255,0.10)', stroke: 'rgba(91,140,255,0.45)', text: '#8b8b95' },
  queued:    { fill: 'rgba(224,179,65,0.15)', stroke: 'rgba(224,179,65,0.60)', text: '#e0b341' },
  rendering: { fill: 'rgba(224,179,65,0.30)', stroke: 'rgba(224,179,65,0.90)', text: '#e0b341' },
  done:      { fill: 'rgba(63,185,80,0.18)',  stroke: 'rgba(63,185,80,0.70)',  text: '#3fb950' },
  error:     { fill: 'rgba(248,81,73,0.16)',  stroke: 'rgba(248,81,73,0.70)',  text: '#f85149' },
};

export default {
  template: `
    <div ref="wrap"
         style="width:100%;height:100%;overflow-x:auto;overflow-y:hidden;
                display:flex;align-items:center;background:#1a1a1e;box-sizing:border-box">
      <canvas ref="canvas"
              style="display:block;flex-shrink:0;cursor:crosshair"
              @mousedown="onDown" @mousemove="onMove" @click="onClick"></canvas>
    </div>
  `,

  props: {
    totalDuration:  { type: Number, default: 60 },
    keyframes:      { type: Array,  default: () => [] },   // [{id, time}]
    segmentStatus:  { type: Object, default: () => ({}) }, // segment id -> status
    segmentPrompts: { type: Object, default: () => ({}) }, // segment id -> has prompt
    selected:       { type: Object, default: () => ({ kind: null, id: null }) },
  },

  data() {
    return {
      kfs: [],          // local working copy of the keyframes
      dragId: null,
      dragOffsetX: 0,
      dragMoved: false,
    };
  },

  watch: {
    // Python pushed new keyframes -> replace the working copy and redraw.
    // Skip while dragging, and when nothing actually changed, so the
    // select round-trip on mousedown can't clobber an in-progress drag.
    keyframes: {
      handler(v) {
        if (this.dragId !== null) return;
        if (JSON.stringify(v) === JSON.stringify(this.kfs)) return;
        this.kfs = v.map(k => ({ ...k }));
        this.resize();
      },
      immediate: true,
      deep: true,
    },
    totalDuration() { this.resize(); },
    segmentStatus:  { handler() { this.draw(); }, deep: true },
    segmentPrompts: { handler() { this.draw(); }, deep: true },
    selected:       { handler() { this.draw(); }, deep: true },
  },

  mounted() {
    console.log('mounted timeline');
    this.ctx = this.$refs.canvas.getContext('2d');
    window.addEventListener('mouseup', this.onUp);
    this._ro = new ResizeObserver(() => this.resize());
    this._ro.observe(this.$refs.wrap);
    this.resize();
  },

  beforeUnmount() {
    window.removeEventListener('mouseup', this.onUp);
    if (this._ro) this._ro.disconnect();
  },

  methods: {
    // ---- geometry ---------------------------------------------------------
    timeToX(t) { return 40 + t * PPS; },
    xToTime(x) { return Math.max(0, (x - 40) / PPS); },

    resize() {
      const c = this.$refs.canvas;
      const w = this.$refs.wrap;
      if (!c || !w) return;
      c.width = Math.max(w.clientWidth, this.totalDuration * PPS + 80);
      c.height = 200;
      this.draw();
    },

    // Segments are derived state: the gap between each pair of consecutive
    // keyframes (sorted by time). IDs are "kfA-kfB" to match the Python side.
    // The prompt is a segment property (on the Python side), not on the keyframes.
    getSegments() {
      const sorted = [...this.kfs].sort((a, b) => a.time - b.time);
      const segs = [];
      for (let i = 0; i < sorted.length - 1; i++) {
        segs.push({
          id: `${sorted[i].id}-${sorted[i + 1].id}`,
          start: sorted[i].time,
          end: sorted[i + 1].time,
        });
      }
      return segs;
    },

    // Which keyframe ids are the *ending* keyframe of a segment that has a
    // prompt. Segment ids are "kfA-kfB", so the last dash-delimited part is
    // the ending keyframe (keyframe ids themselves never contain '-').
    getPromptedEndingKfIds() {
      const ids = new Set();
      const sp = this.segmentPrompts || {};
      for (const segId in sp) {
        if (!sp[segId]) continue;
        const parts = segId.split('-');
        if (parts.length >= 2) ids.add(parts[parts.length - 1]);
      }
      return ids;
    },

    findKfAt(x, y) {
      for (const kf of this.kfs) {
        const kx = this.timeToX(kf.time);
        if (Math.hypot(kx - x, TRACK_Y - y) <= KF_R + 4) return kf;
      }
      return null;
    },

    findSegAt(x, y) {
      if (y < SEG_TOP || y > SEG_TOP + SEG_H) return null;
      for (const s of this.getSegments()) {
        if (x >= this.timeToX(s.start) && x <= this.timeToX(s.end)) return s;
      }
      return null;
    },

    // ---- drawing ----------------------------------------------------------
    roundRect(x, y, w, h, r) {
      const ctx = this.ctx;
      r = Math.min(r, w / 2, h / 2);
      ctx.beginPath();
      ctx.moveTo(x + r, y);
      ctx.arcTo(x + w, y, x + w, y + h, r);
      ctx.arcTo(x + w, y + h, x, y + h, r);
      ctx.arcTo(x, y + h, x, y, r);
      ctx.arcTo(x, y, x + w, y, r);
      ctx.closePath();
    },

    draw() {

        console.log('draw', this.kfs.length);

      const ctx = this.ctx;
      const canvas = this.$refs.canvas;
      if (!ctx || !canvas) return;

      const selKind = this.selected ? this.selected.kind : null;
      const selId = this.selected ? this.selected.id : null;

      ctx.clearRect(0, 0, canvas.width, canvas.height);

      // Keyframe track + tick marks.
      ctx.strokeStyle = '#34343c';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(40, TRACK_Y);
      ctx.lineTo(this.timeToX(this.totalDuration), TRACK_Y);
      ctx.stroke();

      ctx.fillStyle = '#8b8b95';
      ctx.font = '10px -apple-system, sans-serif';
      for (let t = 0; t <= this.totalDuration; t += 5) {
        const x = this.timeToX(t);
        ctx.beginPath();
        ctx.moveTo(x, TRACK_Y - 4);
        ctx.lineTo(x, TRACK_Y + 4);
        ctx.strokeStyle = '#34343c';
        ctx.stroke();
        ctx.fillText(t + 's', x - 8, TRACK_Y + 24);
      }

      // Connecting lines between consecutive keyframes.
      const sorted = [...this.kfs].sort((a, b) => a.time - b.time);
      ctx.strokeStyle = '#5b8cff55';
      ctx.lineWidth = 4;
      for (let i = 0; i < sorted.length - 1; i++) {
        ctx.beginPath();
        ctx.moveTo(this.timeToX(sorted[i].time), TRACK_Y);
        ctx.lineTo(this.timeToX(sorted[i + 1].time), TRACK_Y);
        ctx.stroke();
      }

      // Keyframe dots. Keyframes are bare time markers (no image of their
      // own); the small blue dot marks that the segment *ending* here has a
      // transition prompt set (prompt is a segment property).
      const prompted = this.getPromptedEndingKfIds();
      this.kfs.forEach(kf => {
        const x = this.timeToX(kf.time);
        const isSel = selKind === 'keyframe' && selId === kf.id;
        ctx.beginPath();
        ctx.arc(x, TRACK_Y, KF_R, 0, Math.PI * 2);
        ctx.fillStyle = isSel ? '#5b8cff' : '#c9c9d2';
        ctx.fill();
        ctx.strokeStyle = '#1a1a1e';
        ctx.lineWidth = 2;
        ctx.stroke();
        if (prompted.has(kf.id)) {
          ctx.fillStyle = '#5b8cff';
          ctx.beginPath();
          ctx.arc(x, TRACK_Y - 18, 3, 0, Math.PI * 2);
          ctx.fill();
        }
      });

      this.drawVideoRow();
    },

    drawVideoRow() {
      const ctx = this.ctx;
      const selKind = this.selected ? this.selected.kind : null;
      const selId = this.selected ? this.selected.id : null;

      ctx.font = '10px -apple-system, sans-serif';
      ctx.fillStyle = '#8b8b95';
      ctx.fillText('KEYFRAMES', 8, TRACK_Y - 16);
      ctx.fillText('VIDEO', 8, SEG_TOP + 14);

      // Faint full-length rail behind the segment blocks.
      ctx.strokeStyle = '#2a2a30';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(40, SEG_TOP + SEG_H / 2);
      ctx.lineTo(this.timeToX(this.totalDuration), SEG_TOP + SEG_H / 2);
      ctx.stroke();

      const segs = this.getSegments();
      if (segs.length === 0) {
        ctx.save();
        ctx.strokeStyle = '#34343c';
        ctx.setLineDash([4, 4]);
        ctx.lineWidth = 1;
        ctx.strokeRect(40, SEG_TOP, this.timeToX(this.totalDuration) - 40, SEG_H);
        ctx.restore();
        ctx.fillStyle = '#55555e';
        ctx.fillText('Add two or more keyframes to create a video segment', 52, SEG_TOP + SEG_H / 2 + 3);
        return;
      }

      for (const s of segs) {
        const x1 = this.timeToX(s.start) + 2;
        const w = Math.max(2, this.timeToX(s.end) - x1 - 2);
        const status = (this.segmentStatus && this.segmentStatus[s.id]) || 'empty';
        const colors = SEG_COLORS[status] || SEG_COLORS.empty;
        const selected = selKind === 'segment' && selId === s.id;

        this.roundRect(x1, SEG_TOP, w, SEG_H, 6);
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
    },

    // ---- events out -------------------------------------------------------
    emitChange() {
      this.$emit('change', { keyframes: this.kfs.map(k => ({ ...k })) });
    },
    emitSelect(kind, id) {
      this.$emit('select', { kind, id });
    },

    // ---- mouse handling ---------------------------------------------------
    pos(e) {
      const r = this.$refs.canvas.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    },

    onDown(e) {
      const { x, y } = this.pos(e);
      const hit = this.findKfAt(x, y);
      if (hit) {
        this.dragId = hit.id;
        this.dragOffsetX = this.timeToX(hit.time) - x;
        this.dragMoved = false;
        this.emitSelect('keyframe', hit.id);
      }
    },

    onMove(e) {
      const { x, y } = this.pos(e);
      const canvas = this.$refs.canvas;
      if (this.dragId !== null) {
        this.dragMoved = true;
        const kf = this.kfs.find(k => k.id === this.dragId);
        if (kf) {
          kf.time = Math.round(
            Math.min(this.totalDuration, Math.max(0, this.xToTime(x + this.dragOffsetX))) * 10
          ) / 10;
          this.draw(); // local only — nothing is sent while dragging
        }
        canvas.style.cursor = 'grabbing';
        return;
      }
      canvas.style.cursor = (this.findKfAt(x, y) || this.findSegAt(x, y)) ? 'pointer' : 'crosshair';
    },

    onUp() {
      const wasDragging = this.dragId !== null;
      this.dragId = null;
      if (wasDragging && this.dragMoved) this.emitChange(); // settled
      if (this.$refs.canvas) this.$refs.canvas.style.cursor = 'crosshair';
    },

    onClick(e) {
      // A completed drag ends with a click on the canvas too — ignore it so
      // releasing over a segment block doesn't accidentally select it.
      if (this.dragMoved) { this.dragMoved = false; return; }

      const { x, y } = this.pos(e);

      const kf = this.findKfAt(x, y);
      if (kf) return this.emitSelect('keyframe', kf.id);

      const seg = this.findSegAt(x, y);
      if (seg) return this.emitSelect('segment', seg.id);

      if (Math.abs(y - TRACK_Y) < 20) {
        // Unique id (no '-' so "kfA-kfB" segment ids stay parseable).
        const id = 'kf' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5);
        this.kfs.push({
          id,
          time: Math.round(this.xToTime(x) * 10) / 10,
        });
        this.resize();
        this.emitChange();
        return this.emitSelect('keyframe', id);
      }

      // Click on empty space clears the selection.
      this.emitSelect(null, null);
    },
  },
};