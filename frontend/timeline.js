// Timeline widget for NiceGUI (loaded via `Element, component='timeline.js'`).
//
// Python owns the state. Props in: segments ([{id, duration}], laid end to end
// from t=0), segmentStatus, segmentPrompts, selected (segment id or null).
// Events out: 'select' ({id}), 'resize' ({id, duration}), 'reorder' ({from, to}).
// Continuous mousemove while dragging stays local to the widget; only settled
// actions (mouseup after a drag, clicking) emit events.

const PPS = 40;
const LEFT = 40;
const RULER_Y = 28;
const SEG_TOP = 56;
const SEG_H = 64;
const EDGE_TOL = 10;      // grab tolerance (px) for a segment's right edge
const MIN_DURATION = 0.5; // keep in sync with backend/plan_ops.py
const MIN_VISIBLE = 30;   // the ruler always spans at least this many seconds
const TAIL_PAD = 20;      // empty room after the last segment (seconds)

const round1 = v => Math.round(v * 10) / 10;

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
              style="display:block;flex-shrink:0;cursor:default"
              @mousedown="onDown" @mousemove="onMove" @click="onClick"></canvas>
    </div>
  `,

  props: {
    segments:       { type: Array,  default: () => [] },   // [{id, duration}]
    segmentStatus:  { type: Object, default: () => ({}) }, // segment id -> status
    segmentPrompts: { type: Object, default: () => ({}) }, // segment id -> has prompt
    selected:       { type: String, default: null },       // selected segment id
  },

  data() {
    return {
      segs: [],            // local working copy ([{id, duration}])
      // Edge-resize drag: the segment whose right edge is grabbed.
      resizeId: null,
      resizeStart: 0,      // that segment's start time (fixed while resizing)
      resizeOffsetX: 0,
      resizeMoved: false,
      // Reorder drag: a block is grabbed (segDragFrom >= 0) and becomes
      // "engaged" once the pointer moves past a small threshold (so a plain
      // click still selects). dropIndex is the boundary the pointer is nearest
      // (insertion indicator); dropTo is the slot it will occupy after the move.
      segDragFrom: -1,
      segDragEngaged: false,
      dropIndex: -1,
      dropTo: -1,
      downX: 0,
      downY: 0,
      suppressNextClick: false,
    };
  },

  watch: {
    // Python pushed new segments -> replace the working copy and redraw.
    // Skipped mid-resize, and when nothing changed, so echoes can't clobber a drag.
    segments: {
      handler(v) {
        if (this.resizeId !== null) return;
        const next = v.map(s => ({ id: s.id, duration: s.duration }));
        if (JSON.stringify(next) === JSON.stringify(this.segs)) return;
        this.segs = next;
        this.resize();
      },
      immediate: true,
      deep: true,
    },
    segmentStatus:  { handler() { this.draw(); }, deep: true },
    segmentPrompts: { handler() { this.draw(); }, deep: true },
    selected() { this.draw(); },
  },

  mounted() {
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
    timeToX(t) { return LEFT + t * PPS; },
    xToTime(x) { return Math.max(0, (x - LEFT) / PPS); },

    // Segments are laid end to end from t=0; start = sum of earlier durations.
    layout() {
      let t = 0;
      return this.segs.map(s => {
        const start = t;
        t = round1(t + s.duration);
        return { id: s.id, start, end: t };
      });
    },
    totalTime() {
      const l = this.layout();
      return l.length ? l[l.length - 1].end : 0;
    },
    // Slot boundaries: [0, end0, end1, ...] -> segments + 1 of them.
    bounds() {
      return [0, ...this.layout().map(s => s.end)];
    },

    resize() {
      const c = this.$refs.canvas;
      const w = this.$refs.wrap;
      if (!c || !w) return;
      c.width = Math.max(w.clientWidth, this.timeToX(this.totalTime() + TAIL_PAD) + LEFT);
      c.height = SEG_TOP + SEG_H + 40;
      this.draw();
    },

    // The right edge of a segment within EDGE_TOL of x (inside the row band).
    // Dragging it resizes that segment; later segments ripple.
    findResizeHandleAt(x, y) {
      if (y < SEG_TOP || y > SEG_TOP + SEG_H) return null;
      let best = null, bestD = Infinity;
      for (const s of this.layout()) {
        const d = Math.abs(this.timeToX(s.end) - x);
        if (d < bestD && d <= EDGE_TOL) { best = s; bestD = d; }
      }
      return best;
    },

    findSegAt(x, y) {
      if (y < SEG_TOP || y > SEG_TOP + SEG_H) return null;
      for (const s of this.layout()) {
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
      const ctx = this.ctx;
      const canvas = this.$refs.canvas;
      if (!ctx || !canvas) return;
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      // Ruler: baseline + a tick and label every 5s.
      const rulerEnd = Math.max(MIN_VISIBLE, this.totalTime() + TAIL_PAD);
      ctx.strokeStyle = '#34343c';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(LEFT, RULER_Y);
      ctx.lineTo(this.timeToX(rulerEnd), RULER_Y);
      ctx.stroke();

      ctx.fillStyle = '#8b8b95';
      ctx.font = '10px -apple-system, sans-serif';
      for (let t = 0; t <= rulerEnd; t += 5) {
        const x = this.timeToX(t);
        ctx.beginPath();
        ctx.moveTo(x, RULER_Y - 4);
        ctx.lineTo(x, RULER_Y + 4);
        ctx.stroke();
        ctx.fillText(t + 's', x - 8, RULER_Y - 10);
      }

      this.drawVideoRow();
    },

    drawVideoRow() {
      const ctx = this.ctx;
      ctx.font = '10px -apple-system, sans-serif';

      const segs = this.layout();
      if (segs.length === 0) {
        ctx.save();
        ctx.strokeStyle = '#34343c';
        ctx.setLineDash([4, 4]);
        ctx.lineWidth = 1;
        ctx.strokeRect(LEFT, SEG_TOP, this.timeToX(MIN_VISIBLE) - LEFT, SEG_H);
        ctx.restore();
        ctx.fillStyle = '#55555e';
        ctx.fillText('No video segments yet — press Insert', LEFT + 12, SEG_TOP + SEG_H / 2 + 3);
        return;
      }

      // While a reorder drag is engaged, the grabbed block renders "lifted".
      const liftedId =
        this.segDragEngaged && this.segDragFrom >= 0 && segs[this.segDragFrom]
          ? segs[this.segDragFrom].id
          : null;

      for (const s of segs) {
        const x1 = this.timeToX(s.start) + 2;
        const w = Math.max(2, this.timeToX(s.end) - x1 - 2);
        const status = (this.segmentStatus && this.segmentStatus[s.id]) || 'empty';
        const colors = SEG_COLORS[status] || SEG_COLORS.empty;
        const isSelected = this.selected === s.id;
        const lifted = s.id === liftedId;

        ctx.save();
        if (lifted) {
          ctx.globalAlpha = 0.35;
          ctx.setLineDash([5, 4]);
        }
        this.roundRect(x1, SEG_TOP, w, SEG_H, 6);
        ctx.fillStyle = colors.fill;
        ctx.fill();
        ctx.lineWidth = isSelected ? 2 : 1;
        ctx.strokeStyle = isSelected ? '#e6e6ea' : colors.stroke;
        ctx.stroke();

        if (w >= 36) {
          const cx = x1 + w / 2;
          const cy = SEG_TOP + SEG_H / 2;
          const dur = (s.end - s.start).toFixed(1) + 's';
          ctx.textAlign = 'center';
          if (w >= 90 && status !== 'empty') {
            ctx.fillStyle = '#c9c9d2';
            ctx.fillText(dur, cx, cy - 2);
            ctx.fillStyle = colors.text;
            ctx.fillText(status, cx, cy + 10);
          } else {
            ctx.fillStyle = '#c9c9d2';
            ctx.fillText(dur, cx, cy + 3);
          }
        }

        // Small blue corner dot: this segment has a transition prompt set.
        if (w >= 16 && this.segmentPrompts && this.segmentPrompts[s.id]) {
          ctx.fillStyle = '#5b8cff';
          ctx.beginPath();
          ctx.arc(x1 + w - 8, SEG_TOP + 8, 3, 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.restore();
      }

      this.drawReorderIndicator();
    },

    drawReorderIndicator() {
      if (!this.segDragEngaged || this.dropIndex < 0) return;
      if (this.dropTo === this.segDragFrom) return; // no-op drop
      const ctx = this.ctx;
      const bx = this.timeToX(this.bounds()[this.dropIndex]);

      ctx.save();
      ctx.strokeStyle = '#5b8cff';
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(bx, SEG_TOP - 8);
      ctx.lineTo(bx, SEG_TOP + SEG_H + 8);
      ctx.stroke();
      const cy = SEG_TOP - 13;
      ctx.fillStyle = '#5b8cff';
      ctx.beginPath();
      ctx.moveTo(bx, cy - 6);
      ctx.lineTo(bx + 6, cy);
      ctx.lineTo(bx, cy + 6);
      ctx.lineTo(bx - 6, cy);
      ctx.closePath();
      ctx.fill();
      ctx.restore();
    },

    // ---- events out -------------------------------------------------------
    emitSelect(id) { this.$emit('select', { id }); },
    emitResize(id, duration) { this.$emit('resize', { id, duration }); },
    emitReorder(from, to) { this.$emit('reorder', { from, to }); },

    // ---- mouse handling ---------------------------------------------------
    pos(e) {
      const r = this.$refs.canvas.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    },

    onDown(e) {
      const { x, y } = this.pos(e);
      this.downX = x;
      this.downY = y;
      this.resizeMoved = false;

      // Grabbed a segment's right edge -> resize it (selection is unchanged).
      const h = this.findResizeHandleAt(x, y);
      if (h) {
        this.resizeId = h.id;
        this.resizeStart = h.start;
        this.resizeOffsetX = this.timeToX(h.end) - x;
        return;
      }

      // Grabbed a block interior -> potential reorder drag (engages on movement).
      const seg = this.findSegAt(x, y);
      if (seg) {
        const idx = this.layout().findIndex(s => s.id === seg.id);
        if (idx >= 0) {
          this.segDragFrom = idx;
          this.segDragEngaged = false;
          this.dropIndex = -1;
          this.dropTo = -1;
        }
      }
    },

    onMove(e) {
      const { x, y } = this.pos(e);
      const canvas = this.$refs.canvas;

      if (this.resizeId !== null) {
        this.resizeMoved = true;
        const seg = this.segs.find(s => s.id === this.resizeId);
        if (seg) {
          const end = this.xToTime(x + this.resizeOffsetX);
          seg.duration = Math.max(MIN_DURATION, round1(end - this.resizeStart));
          this.resize(); // local only — grows the canvas if needed, then redraws
        }
        canvas.style.cursor = 'ew-resize';
        return;
      }

      if (this.segDragFrom >= 0) {
        if (!this.segDragEngaged) {
          if (Math.hypot(x - this.downX, y - this.downY) <= 4) return;
          this.segDragEngaged = true;
        }
        // Snap to the nearest slot boundary. Dropping at boundary b inserts
        // before original slot b, so a segment dragged from an earlier slot
        // lands one to the left ("release just past itself" is a no-op).
        const bounds = this.bounds();
        const n = bounds.length;
        if (n >= 3) { // at least two segments to reorder
          let best = -1, bestD = Infinity;
          for (let k = 0; k < n; k++) {
            const d = Math.abs(this.timeToX(bounds[k]) - x);
            if (d < bestD) { bestD = d; best = k; }
          }
          this.dropIndex = best;
          const to = best - (this.segDragFrom < best ? 1 : 0);
          this.dropTo = Math.max(0, Math.min(n - 2, to));
        } else {
          this.dropIndex = -1;
          this.dropTo = -1;
        }
        this.draw();
        canvas.style.cursor = 'grabbing';
        return;
      }

      if (this.findResizeHandleAt(x, y)) canvas.style.cursor = 'ew-resize';
      else if (this.findSegAt(x, y)) canvas.style.cursor = 'grab';
      else canvas.style.cursor = 'default';
    },

    onUp() {
      if (this.resizeId !== null) {
        const id = this.resizeId;
        this.resizeId = null;
        if (this.resizeMoved) {
          const seg = this.segs.find(s => s.id === id);
          if (seg) this.emitResize(id, seg.duration); // settled
        }
      }

      if (this.segDragFrom >= 0) {
        let didReorder = false;
        if (this.segDragEngaged && this.dropTo >= 0 && this.dropTo !== this.segDragFrom) {
          this.emitReorder(this.segDragFrom, this.dropTo);
          didReorder = true;
        }
        this.segDragFrom = -1;
        this.segDragEngaged = false;
        this.dropIndex = -1;
        this.dropTo = -1;
        if (didReorder) this.suppressNextClick = true; // swallow the click that follows
        else this.draw();                              // clear indicator + lifted styling
      }

      if (this.$refs.canvas) this.$refs.canvas.style.cursor = 'default';
    },

    onClick(e) {
      // A completed drag ends with a click too — ignore it.
      if (this.resizeMoved) { this.resizeMoved = false; return; }
      if (this.suppressNextClick) { this.suppressNextClick = false; return; }

      const { x, y } = this.pos(e);
      if (y < SEG_TOP || y > SEG_TOP + SEG_H) return; // outside the row: inert

      const seg = this.findSegAt(x, y);
      this.emitSelect(seg ? seg.id : null); // empty space clears the selection
    },
  },
};