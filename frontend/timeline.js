// Timeline widget for NiceGUI (loaded via `Element, component='timeline.js'`).
//
// Python owns the state. Props in: totalDuration, keyframes, segmentStatus,
// selected. Events out: 'select' ({kind, id}) and 'change' ({keyframes}).
// Continuous mousemove while dragging stays local to the widget; only settled
// actions (mouseup after a drag, clicking) emit events. The top keyframe track
// is inert — all interaction happens on the video segment row below it.

const PPS = 40;
const TRACK_Y = 60;
const SEG_TOP = 112;  // top of the video segment row (below the keyframe track)
const SEG_H = 46;     // height of a segment block
const EDGE_TOL = 10;  // grab tolerance (px) for segment-boundary edges. Tighter than the
                      // keyframe dot's, since those targets are invisible and their grab
                      // zones must not swallow neighbouring block interiors.

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
              style="display:block;flex-shrink:0;cursor:default"
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
      // Segment reorder-drag state. A segment is grabbed (segDragFrom >= 0),
      // becomes "engaged" once the pointer moves past a small threshold (so a
      // plain click still just selects it). dropIndex is the keyframe boundary
      // the pointer is nearest (draws the insertion indicator); dropTo is the
      // final slot index the segment will occupy after the move.
      segDragFrom: -1,
      segDragEngaged: false,
      dropIndex: -1,
      dropTo: -1,
      downX: 0,
      downY: 0,
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

    // Nearest segment-boundary edge within EDGE_TOL of x, reported only inside the row
    // band. Every interior boundary between consecutive segments is a shared keyframe, so
    // grabbing one re-uses the existing drag mechanics verbatim; equidistant edges resolve
    // to the earlier keyframe (timeline-sorted iteration order).
    findBoundaryKfAt(x, y) {
      if (y < SEG_TOP || y > SEG_TOP + SEG_H) return null;
      const sorted = [...this.kfs].sort((a, b) => a.time - b.time);
      let best = null, bestD = Infinity;
      for (const kf of sorted) {
        const d = Math.abs(this.timeToX(kf.time) - x);
        if (d < bestD && d <= EDGE_TOL) { best = kf; bestD = d; }
      }
      return best;
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

      // The keyframe track is inert — only the ruler (ticks + time labels)
      // renders here and clicks on it do nothing. All interaction happens in
      // the video row below: click a block to select it, drag a shared
      // boundary edge to move that keyframe, or drag a block to reorder.

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
        ctx.fillText('No video segments yet', 52, SEG_TOP + SEG_H / 2 + 3);
        return;
      }

      // While a reorder drag is engaged, the grabbed block renders "lifted"
      // (faded + dashed) so it reads as picked up; the insertion indicator
      // (drawn after this row) shows where it will land.
      const liftedId =
        this.segDragEngaged && this.segDragFrom >= 0 && segs[this.segDragFrom]
          ? segs[this.segDragFrom].id
          : null;

      for (const s of segs) {
        const x1 = this.timeToX(s.start) + 2;
        const w = Math.max(2, this.timeToX(s.end) - x1 - 2);
        const status = (this.segmentStatus && this.segmentStatus[s.id]) || 'empty';
        const colors = SEG_COLORS[status] || SEG_COLORS.empty;
        const selected = selKind === 'segment' && selId === s.id;
        const lifted = s.id === liftedId;

        ctx.save();
        if (lifted) {
          ctx.globalAlpha = 0.35;
          ctx.setLineDash([5, 4]);
        }
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
        }

        // Small blue corner dot: this segment has a transition prompt set.
        if (w >= 16 && this.segmentPrompts && this.segmentPrompts[s.id]) {
          ctx.fillStyle = '#5b8cff';
          ctx.beginPath();
          ctx.arc(x1 + w - 8, SEG_TOP + 8, 3, 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.restore(); // also restores textAlign / alpha / dash
      }

      this.drawReorderIndicator();
    },

    // Vertical insertion marker at the slot boundary the dragged segment will
    // occupy — drawn on top of the row so it's always visible.
    drawReorderIndicator() {
      if (!this.segDragEngaged || this.dropIndex < 0) return;
      // A no-op drop (lands where it already is) shows no insertion line.
      if (this.dropTo === this.segDragFrom) return;
      const ctx = this.ctx;
      const sorted = [...this.kfs].sort((a, b) => a.time - b.time);
      if (this.dropIndex >= sorted.length) return;
      const bx = this.timeToX(sorted[this.dropIndex].time);

      ctx.save();
      ctx.strokeStyle = '#5b8cff';
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(bx, SEG_TOP - 8);
      ctx.lineTo(bx, SEG_TOP + SEG_H + 8);
      ctx.stroke();
      // Diamond cap at the top so it reads as an insertion point.
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
    emitChange() {
      this.$emit('change', { keyframes: this.kfs.map(k => ({ ...k })) });
    },
    emitSelect(kind, id) {
      this.$emit('select', { kind, id });
    },
    // A segment was drag-reordered. `from` is the slot it left and `to` the
    // slot it lands in (both 0-based positions among consecutive keyframe
    // pairs). Python permutes which segment occupies each positional slot.
    emitReorder(from, to) {
      this.$emit('reorder', { from, to });
    },

    // ---- mouse handling ---------------------------------------------------
    pos(e) {
      const r = this.$refs.canvas.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    },

    onDown(e) {
      const { x, y } = this.pos(e);
      this.downX = x;
      this.downY = y;
      // Grabbed a video segment boundary edge — that edge is a shared keyframe, so this
      // grabs it as an ordinary keyframe drag. Interior grabs below stay reorder drags.
      // Grabbing the handle must not change what's selected: keep the current video
      // segment selection (no keyframe form) and just start resizing it.
      const bnd = this.findBoundaryKfAt(x, y);
      if (bnd) {
        this.dragId = bnd.id;
        this.dragOffsetX = this.timeToX(bnd.time) - x;
        this.dragMoved = false;
        return;
      }

      // Grabbed a video segment block — start a potential reorder drag. It only
      // "engages" (and stops being a plain click) once the pointer actually moves.
      const seg = this.findSegAt(x, y);
      if (seg) {
        const idx = this.getSegments().findIndex(s => s.id === seg.id);
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
      if (this.segDragFrom >= 0) {
        // Not yet engaged: require real movement so a plain click still selects.
        if (!this.segDragEngaged) {
          if (Math.hypot(x - this.downX, y - this.downY) <= 4) return;
          this.segDragEngaged = true;
        }
        // Snap to the nearest keyframe boundary (there are m+1 of them, one per
        // keyframe — including the right end). Dropping at boundary b inserts
        // the segment before original-slot-b; a segment dragged from an earlier
        // slot therefore lands one left. This makes "release just past itself"
        // a no-op instead of jumping over the neighbour.
        const sorted = [...this.kfs].sort((a, b) => a.time - b.time);
        const n = sorted.length; // number of boundaries (m+1)
        if (n >= 3) {            // at least two segments to reorder
          let best = -1, bestD = Infinity;
          for (let k = 0; k < n; k++) {
            const d = Math.abs(this.timeToX(sorted[k].time) - x);
            if (d < bestD) { bestD = d; best = k; }
          }
          this.dropIndex = best; // boundary index, for the indicator
          const to = best - (this.segDragFrom < best ? 1 : 0);
          this.dropTo = Math.max(0, Math.min(n - 2, to));
        } else {
          this.dropIndex = -1;   // a single segment has nowhere to move to
          this.dropTo = -1;
        }
        this.draw(); // show the insertion indicator + lifted block
        canvas.style.cursor = 'grabbing';
        return;
      }
      if (this.findBoundaryKfAt(x, y)) canvas.style.cursor = 'ew-resize';
      else if (this.findSegAt(x, y)) canvas.style.cursor = 'grab';
      else canvas.style.cursor = 'default';
    },

    onUp() {
      const wasDragging = this.dragId !== null;
      this.dragId = null;
      if (wasDragging && this.dragMoved) this.emitChange(); // settled

      // Finish a segment reorder drag: emit only when it actually changed slot.
      let didReorder = false;
      if (this.segDragFrom >= 0) {
        if (
          this.segDragEngaged &&
          this.dropTo >= 0 &&
          this.dropTo !== this.segDragFrom
        ) {
          this.emitReorder(this.segDragFrom, this.dropTo);
          didReorder = true;
        }
        this.segDragFrom = -1;
        this.segDragEngaged = false;
        this.dropIndex = -1;
        this.dropTo = -1;
        if (didReorder) {
          // Swallow the click that follows this mouseup so it doesn't also
          // select a segment at the drop point.
          this.suppressNextClick = true;
        } else {
          this.draw(); // clear the indicator + lifted styling
        }
      }

      if (this.$refs.canvas) this.$refs.canvas.style.cursor = 'default';
    },

    onClick(e) {
      // A completed drag ends with a click on the canvas too — ignore it so
      // releasing over a segment block doesn't accidentally select it.
      if (this.dragMoved) { this.dragMoved = false; return; }
      if (this.suppressNextClick) { this.suppressNextClick = false; return; }

      const { x, y } = this.pos(e);

      // The keyframe track is inert — clicks outside the video segment row do
      // nothing: no adding keyframes, no selecting, no clearing.
      if (y < SEG_TOP || y > SEG_TOP + SEG_H) return;

      const seg = this.findSegAt(x, y);
      if (seg) return this.emitSelect('segment', seg.id);

      // Click on empty space in the video row clears the selection.
      this.emitSelect(null, null);
    },
  },
};