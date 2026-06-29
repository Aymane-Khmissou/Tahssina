/**
 * Tahssina - Client-Side JavaScript
 */
'use strict';

const $ = (sel, ctx = document) => ctx.querySelector(sel);
const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

function formatMAD(amount) { return parseFloat(amount).toFixed(2) + ' MAD'; }

function formatMinutes(mins) {
  if (mins < 1)  return 'Now';
  if (mins < 60) return Math.round(mins) + ' min';
  const h = Math.floor(mins / 60), m = Math.round(mins % 60);
  return m > 0 ? h + 'h ' + m + 'min' : h + 'h';
}

function escHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function showToast(msg, type) {
  type = type || 'success';
  let c = $('.flash-container');
  if (!c) { c = document.createElement('div'); c.className = 'flash-container'; document.body.appendChild(c); }
  const el = document.createElement('div');
  el.className = 'flash flash-' + type;
  el.innerHTML = '<span>' + escHtml(msg) + '</span><button class="flash-close">✕</button>';
  el.querySelector('.flash-close').addEventListener('click', () => el.remove());
  c.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

function initFlashMessages() {
  $$('.flash').forEach(el => {
    const btn = el.querySelector('.flash-close');
    if (btn) btn.addEventListener('click', () => el.remove());
    setTimeout(() => el.remove(), 5000);
  });
}

function initRoleToggle() {
  const sel = $('#role-select');
  const fields = $('.barber-fields');
  if (!sel || !fields) return;
  function toggle() { fields.classList.toggle('visible', sel.value === 'barber'); }
  sel.addEventListener('change', toggle);
  toggle();
}

function initBookingCalculator() {
  const items = $$('.service-item');
  if (!items.length) return;
  const totalEl = $('#price-total');
  const durEl   = $('#duration-total');
  const linesEl = $('#price-line-items');
  const submitBtn = $('#booking-submit');

  function recalc() {
    let total = 0, dur = 0;
    const lines = [];
    items.forEach(item => {
      if (item.classList.contains('selected')) {
        total += parseFloat(item.dataset.price || 0);
        dur   += parseInt(item.dataset.duration || 0, 10);
        lines.push({ name: item.dataset.name, price: parseFloat(item.dataset.price) });
      }
    });
    if (totalEl) {
      totalEl.classList.remove('bump');
      void totalEl.offsetWidth;
      totalEl.textContent = formatMAD(total);
      totalEl.classList.add('bump');
      setTimeout(() => totalEl.classList.remove('bump'), 400);
    }
    if (durEl)   durEl.textContent = dur > 0 ? '~' + dur + ' min' : '--';
    if (linesEl) linesEl.innerHTML = lines.map(l => '<div class="price-line"><span>' + escHtml(l.name) + '</span><span>' + formatMAD(l.price) + '</span></div>').join('');
    if (submitBtn) submitBtn.disabled = lines.length === 0;
  }

  items.forEach(item => {
    item.addEventListener('click', () => {
      item.classList.toggle('selected');
      const cb = item.querySelector('input[type="checkbox"]');
      if (cb) cb.checked = item.classList.contains('selected');
      recalc();
    });
  });
  recalc();
}

// ═══════════════════════════════════════════════════════════════════════════
//  DATE & TIME PICKER — date pills + time slot buttons
// ═══════════════════════════════════════════════════════════════════════════

const DAY_NAMES_SHORT = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
const MONTH_NAMES_SHORT = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

function initDateTimePicker() {
  const form = $('#booking-form');
  if (!form) return;

  const pillsRow   = $('#date-pills-row');
  const slotsList  = $('#time-slots-list');
  const closedMsg  = $('#closed-day-msg');
  const hiddenInput = $('#appointment_time');
  const submitBtn  = $('#booking-submit');

  const openTime  = form.dataset.openTime  || '09:00';
  const closeTime = form.dataset.closeTime || '20:00';
  let closedDates = [];
  try { closedDates = JSON.parse(form.dataset.closedDates || '[]'); } catch(_) {}

  const closedMap = {};
  closedDates.forEach(c => { closedMap[c.closed_date] = c.reason || ''; });

  // Fetch fully booked dates from API
  let fullDates = new Set();
  const barberId = form.querySelector('[name="barber_id"]')?.value;
  if (barberId) {
    fetch(`/api/fully-booked/${barberId}`)
      .then(r => r.json())
      .then(data => {
        fullDates = new Set(data.full_dates || []);
        renderPills(); // re-render with full info
      }).catch(() => {});
  }

  // Build next 8 days starting today
  const days = [];
  const today = new Date();
  today.setHours(0,0,0,0);
  for (let i = 0; i < 8; i++) {
    const d = new Date(today);
    d.setDate(d.getDate() + i);
    days.push(d);
  }

  function fmtDateKey(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth()+1).padStart(2,'0');
    const day = String(d.getDate()).padStart(2,'0');
    return `${y}-${m}-${day}`;
  }

  let selectedDate = null;
  let selectedTime = null;

  function renderPills() {
    pillsRow.innerHTML = days.map((d, idx) => {
      const key = fmtDateKey(d);
      const isClosed = !!closedMap[key];
      const isFull = fullDates.has(key);
      const dayName = DAY_NAMES_SHORT[d.getDay()];
      const monthName = MONTH_NAMES_SHORT[d.getMonth()];
      let cls = 'date-pill';
      if (isClosed) cls += ' date-pill-closed';
      else if (isFull) cls += ' date-pill-full';
      return `<button type="button" class="${cls}"
                data-date="${key}" data-idx="${idx}"
                ${isClosed ? 'data-closed-reason="'+escHtml(closedMap[key])+'"' : ''}
                ${isFull && !isClosed ? 'data-full="1"' : ''}>
                <span class="date-pill-day">${dayName}</span>
                <span class="date-pill-num">${d.getDate()}</span>
                <span class="date-pill-month">${monthName}</span>
                ${isFull && !isClosed ? '<span class="date-pill-tag">FULL</span>' : ''}
              </button>`;
    }).join('');

    // Re-attach click listeners
    pillsRow.querySelectorAll('.date-pill').forEach(btn => {
      btn.addEventListener('click', () => handlePillClick(btn));
    });
  }

  function generateTimeSlots(dateKey) {
    const [oh, om] = openTime.split(':').map(Number);
    const [ch, cm] = closeTime.split(':').map(Number);
    const slots = [];
    let cur = oh * 60 + om;
    const end = ch * 60 + cm;
    const now = new Date();
    const isToday = dateKey === fmtDateKey(today);
    const nowMins = now.getHours() * 60 + now.getMinutes();

    while (cur < end) {
      if (!isToday || cur > nowMins + 10) {
        const h = Math.floor(cur / 60);
        const m = cur % 60;
        const hour12 = h % 12 === 0 ? 12 : h % 12;
        const ampm = h < 12 ? 'AM' : 'PM';
        const label = `${hour12}:${String(m).padStart(2,'0')} ${ampm}`;
        const value = `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}`;
        slots.push({ label, value });
      }
      cur += 30;
    }
    return slots;
  }

  // ── Get total duration of selected services ──────────────────────────────
  function getSelectedDuration() {
    let total = 0;
    $$('.service-item.selected').forEach(el => {
      total += parseInt(el.dataset.duration || 30, 10);
    });
    return total || 30; // default 30 min if nothing selected yet
  }

  // Convert "HH:MM" string to minutes since midnight
  function toMins(hhmm) {
    const [h, m] = hhmm.split(':').map(Number);
    return h * 60 + m;
  }

  // Check if a proposed slot [startMins, startMins+duration) overlaps any booked range
  function overlaps(startMins, durationMins, bookedRanges) {
    const endMins = startMins + durationMins;
    for (const b of bookedRanges) {
      const bs = toMins(b.start);
      const be = toMins(b.end);
      // Overlap: proposed start < booked end AND proposed end > booked start
      if (startMins < be && endMins > bs) return true;
    }
    return false;
  }

  // Cache: dateKey -> booked ranges array
  const bookedCache = {};

  async function fetchBookedSlots(dateKey) {
    if (bookedCache[dateKey]) return bookedCache[dateKey];
    try {
      const resp = await fetch(`/api/booked-slots/${barberId}/${dateKey}`);
      const data = await resp.json();
      bookedCache[dateKey] = data.booked || [];
    } catch {
      bookedCache[dateKey] = [];
    }
    return bookedCache[dateKey];
  }

  async function renderTimeSlots(dateKey) {
    if (closedMap[dateKey] !== undefined) {
      slotsList.style.display = 'none';
      closedMsg.style.display = 'block';
      const reason = closedMap[dateKey];
      closedMsg.innerHTML = `
        <span style="font-size:1.3rem">🚫</span>
        <div>
          <div style="font-weight:700;color:var(--red)">This day is closed</div>
          ${reason ? `<div style="font-size:.82rem;color:var(--text-muted);margin-top:3px">"${escHtml(reason)}"</div>` : ''}
          <div style="font-size:.78rem;color:var(--text-muted);margin-top:4px">Please choose another date.</div>
        </div>`;
      hiddenInput.value = '';
      selectedTime = null;
      updateSubmitState();
      return;
    }
    if (fullDates.has(dateKey)) {
      slotsList.style.display = 'none';
      closedMsg.style.display = 'block';
      closedMsg.innerHTML = `
        <span style="font-size:1.3rem">📋</span>
        <div>
          <div style="font-weight:700;color:var(--gold)">Fully Booked</div>
          <div style="font-size:.82rem;color:var(--text-muted);margin-top:3px">This day is fully booked.</div>
          <div style="font-size:.78rem;color:var(--text-muted);margin-top:4px">Submit your booking anyway to join the waitlist — you'll be notified if a slot opens up.</div>
        </div>`;
      const slots = generateTimeSlots(dateKey);
      if (slots.length) hiddenInput.value = `${dateKey}T${slots[0].value}`;
      submitBtn.disabled = false;
      submitBtn.textContent = '📋 Join Waitlist for This Day';
      return;
    }

    // Show loading state
    closedMsg.style.display = 'none';
    slotsList.style.display = 'flex';
    slotsList.innerHTML = '<p class="muted" style="font-size:.85rem;padding:12px 0">Loading available times…</p>';

    const allSlots    = generateTimeSlots(dateKey);
    const bookedRanges = await fetchBookedSlots(dateKey);
    const duration    = getSelectedDuration();

    if (!allSlots.length) {
      slotsList.innerHTML = '<p class="muted" style="font-size:.85rem;padding:12px 0">No slots available for this day.</p>';
      return;
    }

    const [ch, cm] = closeTime.split(':').map(Number);
    const closeMins = ch * 60 + cm;

    let anyAvailable = false;
    slotsList.innerHTML = allSlots.map(s => {
      const startMins = toMins(s.value);
      const endMins   = startMins + duration;
      const tooLate   = endMins > closeMins;   // appointment would end after closing
      const busy      = overlaps(startMins, duration, bookedRanges);
      const disabled  = tooLate || busy;
      if (!disabled) anyAvailable = true;
      return disabled
        ? `<button type="button" class="time-slot-btn time-slot-taken" disabled
             title="${busy ? 'Already booked' : 'Would end after closing time'}">
             ${s.label}
             <span class="slot-taken-label">${busy ? 'Booked' : 'Too late'}</span>
           </button>`
        : `<button type="button" class="time-slot-btn" data-value="${s.value}">${s.label}</button>`;
    }).join('');

    if (!anyAvailable) {
      slotsList.innerHTML = '<p class="muted" style="font-size:.85rem;padding:12px 0">⚠️ No available slots for your selected services on this day. Try a different date or fewer services.</p>';
    }

    // Re-attach slot click listeners
    slotsList.querySelectorAll('.time-slot-btn:not([disabled])').forEach(btn => {
      btn.addEventListener('click', () => {
        $$('.time-slot-btn').forEach(b => b.classList.remove('time-slot-selected'));
        btn.classList.add('time-slot-selected');
        selectedTime = btn.dataset.value;
        updateHidden();
        updateSubmitState();
      });
    });
  }

  function updateSubmitState() {
    if (!submitBtn) return;
    const serviceSelected = $$('.service-item.selected').length > 0;
    const isFull = selectedDate && fullDates.has(selectedDate);
    if (isFull) {
      submitBtn.disabled = !(selectedDate && serviceSelected);
    } else {
      submitBtn.disabled = !(selectedDate && selectedTime && serviceSelected);
    }
  }

  function handlePillClick(pill) {
    if (pill.classList.contains('date-pill-closed')) {
      // Show closed reason but don't select
      const reason = pill.dataset.closedReason || '';
      closedMsg.style.display = 'block';
      closedMsg.innerHTML = `
        <span style="font-size:1.3rem">🚫</span>
        <div>
          <div style="font-weight:700;color:var(--red)">This day is closed</div>
          ${reason ? `<div style="font-size:.82rem;color:var(--text-muted);margin-top:3px">"${escHtml(reason)}"</div>` : ''}
          <div style="font-size:.78rem;color:var(--text-muted);margin-top:4px">Please choose another date.</div>
        </div>`;
      slotsList.style.display = 'none';
      return;
    }
    $$('.date-pill').forEach(p => p.classList.remove('date-pill-selected'));
    pill.classList.add('date-pill-selected');
    selectedDate = pill.dataset.date;
    selectedTime = null;
    submitBtn.textContent = 'Request Queue Slot';
    renderTimeSlots(selectedDate);
    updateHidden();
    updateSubmitState();
  }

  pillsRow.addEventListener('click', (e) => {
    const pill = e.target.closest('.date-pill');
    if (!pill) return;
    handlePillClick(pill);
  });

  slotsList.addEventListener('click', (e) => {
    const btn = e.target.closest('.time-slot-btn');
    if (!btn || btn.disabled || !btn.dataset.value) return;
    $$('.time-slot-btn').forEach(b => b.classList.remove('time-slot-selected'));
    btn.classList.add('time-slot-selected');
    selectedTime = btn.dataset.value;
    updateHidden();
    updateSubmitState();
  });

  function updateHidden() {
    if (selectedDate && selectedTime) {
      hiddenInput.value = `${selectedDate}T${selectedTime}`;
    } else {
      hiddenInput.value = '';
    }
  }

  // When services change: re-render slots (duration changes affect availability)
  document.addEventListener('click', (e) => {
    if (e.target.closest('.service-item')) {
      setTimeout(() => {
        // Clear cache for selected date so we refetch with new duration
        if (selectedDate) {
          delete bookedCache[selectedDate];
          if (!fullDates.has(selectedDate) && closedMap[selectedDate] === undefined) {
            // Reset selected time since duration changed
            selectedTime = null;
            hiddenInput.value = '';
            renderTimeSlots(selectedDate);
          }
        }
        updateSubmitState();
      }, 0);
    }
  });

  renderPills();
  // Auto-select first non-closed day
  const firstOpenIdx = days.findIndex(d => !closedMap[fmtDateKey(d)]);
  if (firstOpenIdx >= 0) {
    const pill = pillsRow.querySelector(`[data-idx="${firstOpenIdx}"]`);
    if (pill) pill.click();
  }
}

function initProgressRing() {
  const ring = $('#progress-ring-fill');
  if (!ring) return;
  const r = parseFloat(ring.getAttribute('r'));
  const circ = 2 * Math.PI * r;
  ring.style.strokeDasharray  = circ;
  ring.style.strokeDashoffset = circ;

  function setProgress(pct) {
    ring.style.strokeDashoffset = circ * (1 - Math.max(0, Math.min(1, pct)));
    ring.style.stroke = pct < 0.2 ? '#E05050' : '#D4AF37';
  }

  const duration = parseInt(document.body.dataset.duration || 0, 10);
  const apptTime = document.body.dataset.apptTime || '';
  const timeEl   = $('#progress-time');

  if (!duration || !apptTime) return;

  function update() {
    const appt    = new Date(apptTime.replace(' ', 'T') + 'Z');
    const elapsed = (Date.now() - appt) / 60000;
    const remain  = Math.max(0, duration - elapsed);
    setProgress(remain / duration);
    if (timeEl) timeEl.textContent = formatMinutes(remain);
    if (remain <= 0) { clearInterval(iv); ring.style.stroke = '#3DBE6C'; }
  }
  update();
  const iv = setInterval(update, 10000);
}

function initWaitDisplay() {
  const waitEl  = $('#wait-display');
  const qId     = document.body.dataset.queueId;
  if (!waitEl || !qId) return;
  setInterval(() => {
    fetch('/api/queue-status/' + qId)
      .then(r => r.json())
      .then(d => {
        if (!d.error) {
          waitEl.textContent = formatMinutes(d.wait_minutes);
          if (d.status !== document.body.dataset.status) location.reload();
        }
      }).catch(() => {});
  }, 30000);
}

function initClientTrackSSE() {
  const bId = document.body.dataset.barberId;
  if (!bId) return;
  const src = new EventSource('/stream/' + bId);
  src.onmessage = (e) => {
    try {
      const d = JSON.parse(e.data);
      if (d.event === 'queue_update') location.reload();
    } catch(_) {}
  };
}

function renderQueueCards(rows, container) {
  if (!rows.length) {
    container.innerHTML = '<div class="empty-state"><div class="empty-state-icon">✂️</div><h3>Queue is clear</h3><p>No active bookings right now.</p></div>';
    return;
  }
  container.innerHTML = rows.map(r => {
    const warn = r.no_show_count >= 4
      ? '<span class="no-show-warning" style="background:rgba(224,80,80,.2);color:var(--red);padding:3px 8px;border-radius:100px;font-size:.72rem;font-weight:700">🔴 BLOCKED ' + r.no_show_count + ' no-shows</span>'
      : r.no_show_count >= 3
      ? '<span class="no-show-warning" style="background:rgba(212,175,55,.15);color:var(--gold);padding:3px 8px;border-radius:100px;font-size:.72rem;font-weight:700">🟡 WARNING ' + r.no_show_count + ' no-shows</span>'
      : r.no_show_count > 0
      ? '<span class="no-show-warning" style="color:var(--text-muted);font-size:.7rem">⚠️ ' + r.no_show_count + ' no-show(s)</span>' : '';
    const loyal = r.loyalty_reward ? '<span class="loyalty-badge">🏆 10th Visit Reward!</span>' : '';

    let ratingStars = '';
    if (r.client_avg_rating) {
      const rounded = Math.round(r.client_avg_rating);
      let stars = '';
      for (let i = 1; i <= 5; i++) stars += (i <= rounded) ? '★' : '☆';
      ratingStars = '<span class="stars" style="font-size:.78rem">' + stars + ' <span class="muted">client rating</span></span>';
    }

    let actions = '';
    if (r.status === 'pending') {
      actions = '<button class="btn btn-success btn-sm" data-action="accept" data-queue-id="' + r.id + '">✓ Accept</button><button class="btn btn-danger btn-sm" data-action="reject" data-queue-id="' + r.id + '">✕ Reject</button>';
    } else if (r.status === 'waiting') {
      actions = '<button class="btn btn-primary btn-sm" data-action="start" data-queue-id="' + r.id + '">▶ Start</button><button class="btn btn-danger btn-sm" data-action="reject" data-queue-id="' + r.id + '">✕ Cancel</button>';
    } else if (r.status === 'ongoing') {
      actions = '<button class="btn btn-success btn-sm" data-action="done" data-queue-id="' + r.id + '">✓ Done</button><button class="btn btn-danger btn-sm" data-action="noshow" data-queue-id="' + r.id + '">⚠ No-Show</button>';
    }

    let rateRow = '';
    if (['pending','waiting','ongoing'].includes(r.status)) {
      let stars = '';
      for (let i = 5; i >= 1; i--) {
        stars += '<input type="radio" name="rate-' + r.id + '" id="rate-' + r.id + '-' + i + '" value="' + i + '">' +
                 '<label for="rate-' + r.id + '-' + i + '" style="font-size:1.2rem">★</label>';
      }
      rateRow = '<div class="rate-client-row" data-queue-id="' + r.id + '" style="padding:10px 16px;border-top:1px solid var(--border);display:flex;align-items:center;gap:8px;flex-wrap:wrap">' +
                '<span class="muted" style="font-size:.78rem">⭐ Rate Client:</span>' +
                '<div class="star-rating star-rating-inline" data-queue-id="' + r.id + '">' + stars + '</div></div>';
    }

    return '<div class="queue-card queue-card-' + r.status + '" id="qcard-' + r.id + '"><div class="queue-card-header"><div class="queue-client-info"><span class="queue-client-name">' + escHtml(r.username) + '</span><span class="queue-client-meta">' + escHtml(r.phone) + '</span>' + ratingStars + '</div><div class="cluster" style="gap:6px">' + warn + loyal + '<span class="badge badge-' + r.status + '">' + r.status + '</span></div></div><div class="queue-card-body"><div class="queue-detail-row"><span>Appointment</span><span>' + r.appointment_time + '</span></div><div class="queue-detail-row"><span>Duration</span><span>' + r.assigned_duration + ' min</span></div><div class="queue-detail-row"><span>Total</span><span style="color:var(--gold);font-weight:700">' + parseFloat(r.total_price).toFixed(2) + ' MAD</span></div></div>' + (actions ? '<div class="queue-actions">' + actions + '</div>' : '') + rateRow + '</div>';
  }).join('');
}

function initBarberDashboard() {
  const bId = document.body.dataset.barberId;
  if (!bId) return;

  const liveEl = $('#live-indicator');
  const src    = new EventSource('/stream/' + bId);
  src.onmessage = (e) => {
    try {
      const d = JSON.parse(e.data);
      if (d.event === 'connected' && liveEl) liveEl.style.display = 'inline-flex';
      if (d.event === 'new_booking' || d.event === 'queue_update') refreshQueueList(bId);
    } catch(_) {}
  };
  src.onerror = () => { if (liveEl) liveEl.style.display = 'none'; };

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-action]');
    if (!btn || !document.body.dataset.barberId) return;
    const action  = btn.dataset.action;
    const queueId = btn.dataset.queueId;
    if (!action || !queueId) return;
    if (action === 'noshow' && !confirm('Mark as No-Show? This increases their penalty count.')) return;
    if (action === 'reject' && !confirm('Reject this booking?')) return;
    btn.disabled = true;

    const fd = new FormData();
    fd.append('action', action);
    fetch('/barber/queue/' + queueId + '/action', { method: 'POST', body: fd })
      .then(r => r.json())
      .then(d => {
        if (d.success) { showToast('Updated!', 'success'); refreshQueueList(bId); }
        else { showToast(d.error || 'Failed.', 'error'); btn.disabled = false; }
      })
      .catch(() => { showToast('Network error.', 'error'); btn.disabled = false; });
  });

  // Rate-client stars: submit immediately on selection
  document.addEventListener('change', (e) => {
    const input = e.target.closest('.rate-client-row .star-rating-inline input[type="radio"]');
    if (!input) return;
    const row = input.closest('.rate-client-row');
    const queueId = row.dataset.queueId;
    const fd = new FormData();
    fd.append('queue_id', queueId);
    fd.append('rating', input.value);
    fetch('/barber/rate-client', { method: 'POST', body: fd })
      .then(r => r.json())
      .then(d => {
        if (d.success) showToast('Client rated!', 'success');
        else showToast(d.error || 'Failed to rate.', 'error');
      })
      .catch(() => showToast('Network error.', 'error'));
  });

  const tog = $('#shop-toggle-input');
  if (tog) {
    tog.addEventListener('change', () => {
      fetch('/barber/toggle-open', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
          const lbl = $('#shop-status-label');
          if (lbl) { lbl.textContent = d.is_open ? 'Open for Business' : 'Shop Closed'; lbl.className = d.is_open ? 'toggle-label gold-text' : 'toggle-label'; }
          const badge = $('#shop-status-badge');
          if (badge) { badge.className = d.is_open ? 'badge badge-open' : 'badge badge-closed'; badge.textContent = d.is_open ? '● Open' : '● Closed'; }
          showToast(d.is_open ? 'Shop is now open.' : 'Shop is now closed.', 'success');
        });
    });
  }
}

function refreshQueueList(bId) {
  const listEl = $('#queue-live-list');
  if (!listEl) return;
  fetch('/api/barber-queue/' + bId)
    .then(r => r.json())
    .then(rows => renderQueueCards(rows, listEl))
    .catch(() => {});
}

function initCharts() {
  const peakCanvas    = $('#peak-hours-chart');
  const revenueCanvas = $('#revenue-chart');

  const baseOpts = {
    responsive: true, maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: { backgroundColor:'#1E1E1E', titleColor:'#F8F9FA', bodyColor:'#A0A0A0', borderColor:'rgba(212,175,55,.3)', borderWidth:1 }
    },
    scales: {
      x: { ticks:{ color:'#A0A0A0', font:{size:11} }, grid:{ color:'rgba(255,255,255,.05)' } },
      y: { ticks:{ color:'#A0A0A0', font:{size:11} }, grid:{ color:'rgba(255,255,255,.05)' }, beginAtZero:true }
    }
  };

  if (peakCanvas) {
    try {
      const raw = JSON.parse(peakCanvas.dataset.peak || '[]');
      new Chart(peakCanvas, {
        type: 'bar',
        data: { labels: raw.map(r => r.hour + ':00'), datasets: [{ data: raw.map(r => r.cnt), backgroundColor:'rgba(212,175,55,.55)', borderColor:'#D4AF37', borderWidth:1.5, borderRadius:4 }] },
        options: baseOpts
      });
    } catch(_) {}
  }

  if (revenueCanvas) {
    try {
      const raw = JSON.parse(revenueCanvas.dataset.revenue || '[]');
      new Chart(revenueCanvas, {
        type: 'line',
        data: { labels: raw.map(r => r.day.slice(5)), datasets: [{ data: raw.map(r => r.total), borderColor:'#D4AF37', backgroundColor:'rgba(212,175,55,.08)', borderWidth:2, pointBackgroundColor:'#D4AF37', pointRadius:4, fill:true, tension:0.4 }] },
        options: baseOpts
      });
    } catch(_) {}
  }
}

function initRateBarberWidget() {
  document.addEventListener('change', (e) => {
    const input = e.target.closest('.star-rating-inline[data-barber-id] input[type="radio"]');
    if (!input) return;
    const row = input.closest('.rate-barber-row');
    const barberId = row.dataset.barberId;
    const rating = input.value;
    const fd = new FormData();
    fd.append('barber_id', barberId);
    fd.append('rating', rating);
    fetch('/client/rate-barber-ajax', { method: 'POST', body: fd })
      .then(r => r.json())
      .then(d => {
        if (d.success) showToast('Thanks for rating!', 'success');
        else showToast(d.error || 'Failed to rate.', 'error');
      })
      .catch(() => showToast('Network error.', 'error'));
  });
}

document.addEventListener('DOMContentLoaded', () => {
  initFlashMessages();
  initRoleToggle();
  initBookingCalculator();
  initDateTimePicker();
  initProgressRing();
  initWaitDisplay();
  initClientTrackSSE();
  initBarberDashboard();
  initRateBarberWidget();
  initCharts();
});

// ═══════════════════════════════════════════════════════════════════════════
//  DASHBOARD TABS
// ═══════════════════════════════════════════════════════════════════════════

function switchTab(name) {
  document.querySelectorAll('.dash-tab').forEach((t, i) => {
    const panels = ['queue','walkin','clients','gallery'];
    t.classList.toggle('active', panels[i] === name);
  });
  document.querySelectorAll('.dash-panel').forEach(p => {
    p.classList.toggle('active', p.id === 'tab-' + name);
  });
  if (name === 'walkin')  loadWalkinQueue();
  if (name === 'clients') loadClientsList();
}

// ═══════════════════════════════════════════════════════════════════════════
//  WALK-IN LIVE QUEUE
// ═══════════════════════════════════════════════════════════════════════════

function loadWalkinQueue() {
  const bId = document.body.dataset.barberId;
  if (!bId) return;
  const el = document.getElementById('walkin-live-list');
  if (!el) return;
  fetch('/api/walkin-queue/' + bId)
    .then(r => r.json())
    .then(rows => renderWalkinCards(rows, el))
    .catch(() => {});
}

function renderWalkinCards(rows, container) {
  if (!rows.length) {
    container.innerHTML = '<div class="empty-state"><div class="empty-state-icon">🚶</div><h3>No walk-ins yet</h3><p>Clients scan your QR code to appear here.</p></div>';
    return;
  }
  container.innerHTML = rows.map(r => {
    let actions = '';
    if (r.status === 'waiting') {
      actions = '<button class="btn btn-primary btn-sm" data-wk-action="start" data-wk-id="' + r.id + '">▶ Start</button>' +
                '<button class="btn btn-danger btn-sm" data-wk-action="cancel" data-wk-id="' + r.id + '">✕</button>';
    } else if (r.status === 'ongoing') {
      actions = '<button class="btn btn-success btn-sm" data-wk-action="done" data-wk-id="' + r.id + '">✓ Done</button>';
    }
    return '<div class="queue-card queue-card-' + r.status + '" style="margin-bottom:10px">' +
      '<div class="queue-card-header"><div class="queue-client-info">' +
      '<span class="queue-client-name">' + escHtml(r.client_name) + '</span>' +
      '<span class="queue-client-meta">📞 ' + escHtml(r.client_phone) + '</span>' +
      '<span class="muted" style="font-size:.72rem">' + r.created_at + '</span>' +
      '</div><span class="badge badge-' + r.status + '">' + r.status + '</span></div>' +
      (actions ? '<div class="queue-actions">' + actions + '</div>' : '') + '</div>';
  }).join('');
}

// Walk-in action buttons
document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-wk-action]');
  if (!btn) return;
  const action = btn.dataset.wkAction;
  const wkId   = btn.dataset.wkId;
  if (!action || !wkId) return;
  btn.disabled = true;
  const fd = new FormData();
  fd.append('action', action);
  fetch('/barber/walkin/' + wkId + '/action', { method: 'POST', body: fd })
    .then(r => r.json())
    .then(d => { if (d.success) { showToast('Updated!', 'success'); loadWalkinQueue(); } else btn.disabled = false; })
    .catch(() => { btn.disabled = false; });
});

// ═══════════════════════════════════════════════════════════════════════════
//  CLIENTS LIST
// ═══════════════════════════════════════════════════════════════════════════

let _allClients = [];

function loadClientsList() {
  const bId = document.body.dataset.barberId;
  if (!bId) return;
  const el = document.getElementById('clients-list-container');
  if (!el) return;
  fetch('/barber/clients')
    .then(r => r.json())
    .then(data => {
      _allClients = data;
      renderClientsList(data, el);
    }).catch(() => {});
}

function renderClientsList(clients, container) {
  if (!clients.length) {
    container.innerHTML = '<div class="empty-state"><p>No clients yet.</p></div>';
    return;
  }
  container.innerHTML = '<div class="stack stack-sm">' + clients.map(c => {
    const stars = c.my_rating > 0
      ? '<span class="stars" style="font-size:.75rem">' + '★'.repeat(Math.round(c.my_rating)) + '☆'.repeat(5 - Math.round(c.my_rating)) + '</span>'
      : '';
    const noShow = c.no_show_count > 0
      ? '<span class="no-show-warning" style="font-size:.7rem">⚠️ ' + c.no_show_count + ' no-show</span>' : '';
    return '<div class="client-list-row">' +
      '<div class="client-list-info">' +
        '<div class="client-list-name">' + escHtml(c.username) + '</div>' +
        '<div class="client-list-meta">📞 ' + escHtml(c.phone) + ' · ' + c.total_visits + ' visit(s) · ' + parseFloat(c.total_spent).toFixed(0) + ' MAD</div>' +
        '<div style="display:flex;gap:6px;margin-top:3px;flex-wrap:wrap">' + stars + noShow + '</div>' +
      '</div>' +
      '<button class="btn btn-secondary btn-sm" onclick="openHistoryPanel(' + c.id + ',\'' + escHtml(c.username) + '\')">📋</button>' +
    '</div>';
  }).join('') + '</div>';
}

// Search filter
document.addEventListener('input', (e) => {
  if (e.target.id !== 'client-search') return;
  const q = e.target.value.toLowerCase();
  const el = document.getElementById('clients-list-container');
  if (!el) return;
  renderClientsList(_allClients.filter(c =>
    c.username.toLowerCase().includes(q) || c.phone.includes(q)
  ), el);
});

// ═══════════════════════════════════════════════════════════════════════════
//  SSE: also refresh walkin queue
// ═══════════════════════════════════════════════════════════════════════════

const _origPushSSE = typeof initBarberDashboard !== 'undefined';
// Patch SSE to also refresh walkin if tab is active
const _walkinSSEHandler = (e) => {
  try {
    const d = JSON.parse(e.data);
    if (d.event === 'walkin' || d.event === 'walkin_update') {
      if (document.getElementById('tab-walkin') &&
          document.getElementById('tab-walkin').classList.contains('active')) {
        loadWalkinQueue();
      }
    }
  } catch(_) {}
};

// Attach to any existing SSE source when dashboard loads
const _origInitDash = window.initBarberDashboard;




// ═══════════════════════════════════════════════════════════════════════
//  BURGER MENU
//  Zid had function f app.js dyalk, w zidha f DOMContentLoaded
// ═══════════════════════════════════════════════════════════════════════
function initBurgerMenu() {
  const burger = document.getElementById('burger');
  const drawer = document.getElementById('nav-drawer');
  if (!burger || !drawer) return;

  burger.addEventListener('click', () => {
    const isOpen = drawer.classList.toggle('open');
    burger.classList.toggle('open', isOpen);
    burger.setAttribute('aria-expanded', isOpen);
    drawer.setAttribute('aria-hidden', !isOpen);
  });

  drawer.querySelectorAll('a').forEach(link => {
    link.addEventListener('click', () => {
      drawer.classList.remove('open');
      burger.classList.remove('open');
      burger.setAttribute('aria-expanded', 'false');
      drawer.setAttribute('aria-hidden', 'true');
    });
  });

  document.addEventListener('click', (e) => {
    if (!burger.contains(e.target) && !drawer.contains(e.target)) {
      drawer.classList.remove('open');
      burger.classList.remove('open');
      burger.setAttribute('aria-expanded', 'false');
      drawer.setAttribute('aria-hidden', 'true');
    }
  });
}

// => Zid initBurgerMenu() f DOMContentLoaded dyalk:
// document.addEventListener('DOMContentLoaded', () => {
//   ...
//   initBurgerMenu();   // <== ZID HAD LSATOUR
// });

document.addEventListener('DOMContentLoaded', () => {
  initFlashMessages();
  initRoleToggle();
  initBookingCalculator();
  initDateTimePicker();
  initProgressRing();
  initWaitDisplay();
  initClientTrackSSE();
  initBarberDashboard();
  initRateBarberWidget();
  initCharts();
  initBurgerMenu(); // ← ZID HAD LSATOUR
});
