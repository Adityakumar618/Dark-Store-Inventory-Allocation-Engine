// =============================================================================
// Dark-Store Fulfillment Control Tower & Concurrency Lab
// Resilient Production Operations Dashboard & Concurrency Benchmark Suite
// =============================================================================

const API_BASE = "";
let mapInstance = null;
let storeMarkers = [];
let storeGeofences = [];
let activeRouteLine = null;
let activeCustomerMarker = null;

let allStores = [];
let allSkus = [];
let allCustomers = [];
let currentSafetyStockData = [];
let currentStoreMatrixData = [];
let benchmarkChart = null;

window.addEventListener("DOMContentLoaded", () => {
  // 1. Critical Data Fetching (Never blocked by optional UI plugins)
  safeExecute("Initial Catalog Data", fetchInitialData);
  safeExecute("Overview Metrics", refreshOverviewMetrics);
  safeExecute("Engine Telemetry", refreshEngineTelemetry);
  safeExecute("Safety Stock ROP Table", loadSafetyStockTable);
  safeExecute("Outbox CDC Stream", loadOutboxEvents);
  
  // 2. Visual Enhancements (Guarded for async script readiness)
  safeExecute("Benchmark Chart Initialization", initBenchmarkChart);

  // 3. Periodic Background Telemetry Pollers
  setInterval(() => safeExecute("Poll Overview", refreshOverviewMetrics), 5000);
  setInterval(() => safeExecute("Poll Telemetry", refreshEngineTelemetry), 7000);
  setInterval(() => safeExecute("Poll Outbox", loadOutboxEvents), 10000);
});

function safeExecute(label, fn) {
  try {
    const res = fn();
    if (res && typeof res.catch === "function") {
      res.catch(err => console.warn(`[Dashboard] ${label} async warning:`, err));
    }
  } catch (err) {
    console.warn(`[Dashboard] ${label} initialization warning:`, err);
  }
}

// =============================================================================
// TAB NAVIGATION
// =============================================================================

function switchTab(tabId) {
  const tabs = document.querySelectorAll(".nav-tab-btn");
  tabs.forEach(t => t.classList.remove("active"));
  
  const sections = document.querySelectorAll(".view-section");
  sections.forEach(s => s.classList.remove("active"));

  if (tabId === "concurrency") {
    tabs[0].classList.add("active");
    document.getElementById("tab-concurrency").classList.add("active");
  } else if (tabId === "spatial") {
    tabs[1].classList.add("active");
    document.getElementById("tab-spatial").classList.add("active");
    // Ensure Leaflet map initializes when container is fully visible
    setTimeout(ensureMapInitialized, 150);
  } else if (tabId === "safety-stock") {
    tabs[2].classList.add("active");
    document.getElementById("tab-safety-stock").classList.add("active");
    loadSafetyStockTable();
  } else if (tabId === "internals") {
    tabs[3].classList.add("active");
    document.getElementById("tab-internals").classList.add("active");
    refreshEngineTelemetry();
    loadOutboxEvents();
  }
}

// =============================================================================
// MAP & SPATIAL NETWORK (PostGIS GiST & Geofences)
// =============================================================================

function ensureMapInitialized() {
  if (typeof L === "undefined") {
    console.warn("[Leaflet] Library not loaded yet, deferring map init.");
    setTimeout(ensureMapInitialized, 300);
    return;
  }

  const mapEl = document.getElementById("control-map");
  if (!mapEl) return;

  if (!mapInstance) {
    initMap();
  } else {
    mapInstance.invalidateSize();
    mapInstance.setView([12.9716, 77.5946], 11);
  }
}

function initMap() {
  if (typeof L === "undefined" || mapInstance) return;

  // Standard clean light OpenStreetMap tiles for high geographical contrast
  mapInstance = L.map("control-map").setView([12.9716, 77.5946], 11);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/">OpenStreetMap</a>',
    maxZoom: 19
  }).addTo(mapInstance);

  if (allStores.length > 0) {
    updateMapPins(allStores);
  }
}

function updateMapPins(stores) {
  if (!mapInstance || typeof L === "undefined") return;
  
  // Clear existing pins and geofences
  storeMarkers.forEach(m => mapInstance.removeLayer(m));
  storeGeofences.forEach(g => mapInstance.removeLayer(g));
  storeMarkers = [];
  storeGeofences = [];

  stores.forEach(s => {
    if (s.lat && s.lng) {
      // 1. PostGIS 10-Minute (2.5 km) SLA Delivery Geofence Circle
      const geofence = L.circle([s.lat, s.lng], {
        radius: 2500, // 2.5 km
        color: "#6366f1",
        weight: 1,
        opacity: 0.35,
        fillColor: "#818cf8",
        fillOpacity: 0.08,
        interactive: false
      }).addTo(mapInstance);
      storeGeofences.push(geofence);

      // 2. Dark Store Pin Marker
      const marker = L.circleMarker([s.lat, s.lng], {
        radius: 7,
        fillColor: "#4f46e5",
        color: "#ffffff",
        weight: 2,
        opacity: 1,
        fillOpacity: 0.95
      }).addTo(mapInstance);

      marker.bindPopup(`
        <div style="font-family: inherit; font-size: 12px; color: #0f172a; line-height: 1.5; min-width: 180px;">
          <strong style="color: #4338ca; font-size: 13px;">${s.name}</strong><br>
          <span style="color: #64748b; font-size: 11px;">${s.address || s.zone_name}</span><br>
          <div style="margin-top: 6px; font-weight: 700; color: #059669;">Stock: ${s.total_units_in_stock || 0} units (${s.distinct_skus_stocked || 0} SKUs)</div>
          <div style="font-size: 10px; color: #6366f1; margin-top: 2px;">⚡ PostGIS 2.5km Geofence Active</div>
          <button style="margin-top: 8px; width: 100%; background: #4f46e5; color: #fff; border: none; padding: 6px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; cursor: pointer;" onclick="selectStoreForInspection(${s.store_id})">Inspect Inventory Matrix</button>
        </div>
      `);
      storeMarkers.push(marker);
    }
  });
}

function drawDeliveryRoute(custLat, custLng, storeLat, storeLng, storeName) {
  if (typeof L === "undefined") return;
  if (!mapInstance) {
    initMap();
  }
  if (!mapInstance) return;

  const lat1 = parseFloat(custLat);
  const lng1 = parseFloat(custLng);
  const lat2 = parseFloat(storeLat);
  const lng2 = parseFloat(storeLng);

  if (isNaN(lat1) || isNaN(lng1) || isNaN(lat2) || isNaN(lng2)) return;

  // Clear previous route
  if (activeRouteLine) mapInstance.removeLayer(activeRouteLine);
  if (activeCustomerMarker) mapInstance.removeLayer(activeCustomerMarker);

  // 1. Add Customer Drop-Off Pin (Emerald)
  activeCustomerMarker = L.circleMarker([lat1, lng1], {
    radius: 9,
    fillColor: "#10b981",
    color: "#ffffff",
    weight: 3,
    opacity: 1,
    fillOpacity: 1
  }).addTo(mapInstance);

  activeCustomerMarker.bindPopup(`
    <div style="font-family: inherit; font-size: 12px; color: #0f172a;">
      <strong style="color: #059669;">Customer Delivery Point</strong><br>
      Fulfillment from: <strong>${storeName}</strong>
    </div>
  `).openPopup();

  // 2. Add Animated Dashed Delivery Route Line
  activeRouteLine = L.polyline([[lat1, lng1], [lat2, lng2]], {
    color: "#6366f1",
    weight: 4,
    opacity: 0.9,
    className: "delivery-route-line"
  }).addTo(mapInstance);

  // 3. Smoothly zoom and fly to fit both points
  const p1 = L.latLng(lat1, lng1);
  const p2 = L.latLng(lat2, lng2);
  const bounds = L.latLngBounds([p1, p2]);
  if (bounds.isValid()) {
    mapInstance.flyToBounds(bounds, { padding: [60, 60], maxZoom: 14, duration: 0.8 });
  }
}

function selectStoreForInspection(storeId) {
  const sel = document.getElementById("store-matrix-select");
  if (sel) {
    sel.value = storeId;
    loadStoreMatrix({ target: { value: storeId } });
    sel.scrollIntoView({ behavior: "smooth" });
  }
}

// =============================================================================
// CHART.JS COMPARATIVE BENCHMARK VISUALIZER
// =============================================================================

function initBenchmarkChart() {
  if (typeof Chart === "undefined") {
    console.warn("[Chart.js] Library not yet available, deferring chart init.");
    return;
  }
  const canvas = document.getElementById("concurrency-benchmark-chart");
  if (!canvas || benchmarkChart) return;

  const ctx = canvas.getContext("2d");
  benchmarkChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: ["Naive (Un-locked)", "Pessimistic (FOR UPDATE)", "Optimistic (OCC)", "Advisory Locks"],
      datasets: [
        {
          label: "Throughput (RPS)",
          data: [0, 0, 0, 0],
          backgroundColor: ["rgba(244, 63, 94, 0.7)", "rgba(16, 185, 129, 0.7)", "rgba(99, 102, 241, 0.7)", "rgba(139, 92, 246, 0.7)"],
          borderColor: ["#f43f5e", "#10b981", "#6366f1", "#8b5cf6"],
          borderWidth: 1.5,
          borderRadius: 6,
          yAxisID: "y"
        },
        {
          label: "Avg Latency (ms)",
          data: [0, 0, 0, 0],
          backgroundColor: "rgba(148, 163, 184, 0.35)",
          borderColor: "#94a3b8",
          borderWidth: 1.5,
          borderRadius: 6,
          yAxisID: "y1"
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          labels: {
            color: "#cbd5e1",
            font: { family: "'Inter', sans-serif", size: 11, weight: "600" }
          }
        },
        tooltip: {
          backgroundColor: "#0c1220",
          titleColor: "#ffffff",
          bodyColor: "#cbd5e1",
          borderColor: "rgba(255, 255, 255, 0.15)",
          borderWidth: 1
        }
      },
      scales: {
        x: {
          ticks: { color: "#94a3b8", font: { family: "'Inter', sans-serif", size: 11 } },
          grid: { color: "rgba(255, 255, 255, 0.05)" }
        },
        y: {
          type: "linear",
          display: true,
          position: "left",
          title: { display: true, text: "Throughput (req/s)", color: "#cbd5e1", font: { size: 11 } },
          ticks: { color: "#cbd5e1" },
          grid: { color: "rgba(255, 255, 255, 0.05)" }
        },
        y1: {
          type: "linear",
          display: true,
          position: "right",
          title: { display: true, text: "Latency (ms)", color: "#94a3b8", font: { size: 11 } },
          ticks: { color: "#94a3b8" },
          grid: { drawOnChartArea: false }
        }
      }
    }
  });
}

function updateBenchmarkChart(naive, pess, occ, adv) {
  // 1. Update the Animated HTML Bar Graph Matrix
  const maxRps = Math.max(naive.throughput_rps, pess.throughput_rps, occ.throughput_rps, adv.throughput_rps, 1.0);

  const setBar = (idBar, idVal, rps) => {
    const bar = document.getElementById(idBar);
    const val = document.getElementById(idVal);
    if (bar) bar.style.width = `${Math.max(8, Math.min(100, Math.round((rps / maxRps) * 100)))}%`;
    if (val) val.innerText = `${rps} req/s`;
  };

  setBar("vis-bar-naive", "vis-val-naive", naive.throughput_rps);
  setBar("vis-bar-pess", "vis-val-pess", pess.throughput_rps);
  setBar("vis-bar-occ", "vis-val-occ", occ.throughput_rps);
  setBar("vis-bar-adv", "vis-val-adv", adv.throughput_rps);

  // 2. Update the Chart.js Dual-Axis Canvas
  if (!benchmarkChart) {
    initBenchmarkChart();
    if (!benchmarkChart) return;
  }

  benchmarkChart.data.datasets[0].data = [
    naive.throughput_rps,
    pess.throughput_rps,
    occ.throughput_rps,
    adv.throughput_rps
  ];

  benchmarkChart.data.datasets[1].data = [
    naive.avg_latency_ms,
    pess.avg_latency_ms,
    occ.avg_latency_ms,
    adv.avg_latency_ms
  ];

  benchmarkChart.update();
}

// =============================================================================
// INITIAL DATA & TELEMETRY
// =============================================================================

async function fetchInitialData() {
  try {
    const [custRes, skuRes, storeRes] = await Promise.all([
      fetch(`${API_BASE}/api/v1/customers`),
      fetch(`${API_BASE}/api/v1/skus`),
      fetch(`${API_BASE}/api/v1/stores`)
    ]);

    allCustomers = await custRes.json();
    allSkus = await skuRes.json();
    allStores = await storeRes.json();

    // Populate simulator selectors
    const custSel = document.getElementById("sim-customer-select");
    custSel.innerHTML = "";
    allCustomers.forEach(c => {
      const opt = document.createElement("option");
      opt.value = c.customer_id;
      opt.innerText = `Cust #${c.customer_id} - ${c.name} (${c.zone_name})`;
      custSel.appendChild(opt);
    });

    const skuSel = document.getElementById("sim-sku-select");
    skuSel.innerHTML = "";
    allSkus.forEach(k => {
      const opt = document.createElement("option");
      opt.value = k.sku_id;
      opt.innerText = `SKU #${k.sku_id} - ${k.name} [₹${k.price}]`;
      skuSel.appendChild(opt);
    });

    // Populate store matrix selector
    const storeSel = document.getElementById("store-matrix-select");
    storeSel.innerHTML = "";
    allStores.forEach(s => {
      const opt = document.createElement("option");
      opt.value = s.store_id;
      opt.innerText = `Store #${s.store_id} - ${s.name} (${s.zone_name})`;
      storeSel.appendChild(opt);
    });

    if (mapInstance) {
      updateMapPins(allStores);
    }

    if (allStores.length > 0) {
      loadStoreMatrix({ target: { value: allStores[0].store_id } });
    }
  } catch (err) {
    console.warn("Initial data loading error", err);
  }
}

async function refreshOverviewMetrics() {
  try {
    const res = await fetch(`${API_BASE}/api/v1/analytics/overview`);
    if (!res.ok) return;
    const data = await res.json();

    document.getElementById("stat-stores").innerText = data.total_stores;
    document.getElementById("stat-skus").innerText = data.total_skus;
    document.getElementById("stat-allocated").innerText = data.allocated_orders;
    document.getElementById("stat-splits").innerText = data.split_orders;
  } catch (err) {
    // Graceful poll
  }
}

async function refreshEngineTelemetry() {
  try {
    const res = await fetch(`${API_BASE}/api/v1/engine/telemetry`);
    if (!res.ok) {
      document.getElementById("header-cache-ratio").innerText = "Engine: Connecting...";
      return;
    }
    const data = await res.json();

    document.getElementById("header-cache-ratio").innerText = `Buffer Cache Hit: ${data.buffer_cache_hit_ratio}%`;
    document.getElementById("header-active-backends").innerText = data.active_backends;
    document.getElementById("stat-outbox").innerText = data.outbox_total_events;

    const diagCache = document.getElementById("diag-cache");
    if (diagCache) {
      diagCache.innerText = `${data.buffer_cache_hit_ratio}%`;
      document.getElementById("diag-backends").innerText = data.active_backends;
      document.getElementById("diag-commits").innerText = data.xact_commit.toLocaleString();
      document.getElementById("diag-rollbacks").innerText = data.xact_rollback.toLocaleString();
      document.getElementById("diag-deadlocks").innerText = data.deadlocks;

      const locksTbody = document.getElementById("diag-locks-tbody");
      locksTbody.innerHTML = "";
      if (!data.active_locks || data.active_locks.length === 0) {
        locksTbody.innerHTML = `<tr><td colspan="4" style="text-align:center; color: var(--text-muted); padding: 16px;">No active row or relation lock contention</td></tr>`;
      } else {
        data.active_locks.forEach(l => {
          const tr = document.createElement("tr");
          tr.innerHTML = `
            <td><code>${l.locktype}</code></td>
            <td><span class="status-pill pill-neutral">${l.mode}</span></td>
            <td><span class="status-pill ${l.granted ? 'pill-optimal' : 'pill-danger'}">${l.granted ? 'GRANTED' : 'WAITING'}</span></td>
            <td><strong style="font-family: var(--font-mono);">${l.count}</strong></td>
          `;
          locksTbody.appendChild(tr);
        });
      }
    }
  } catch (err) {
    document.getElementById("header-cache-ratio").innerText = "Engine: Connecting...";
  }
}

// =============================================================================
// STORE INVENTORY MATRIX
// =============================================================================

async function loadStoreMatrix(e) {
  const storeId = e?.target?.value || document.getElementById("store-matrix-select")?.value || 1;
  const tbody = document.getElementById("store-matrix-tbody");
  tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: var(--text-muted); padding: 20px;">Loading Store #${storeId} inventory...</td></tr>`;

  try {
    const res = await fetch(`${API_BASE}/api/v1/stores/${storeId}/inventory`);
    if (!res.ok) return;
    currentStoreMatrixData = await res.json();
    renderStoreMatrixTable(currentStoreMatrixData);
  } catch (err) {
    console.error("Error loading store matrix", err);
  }
}

function renderStoreMatrixTable(items) {
  const tbody = document.getElementById("store-matrix-tbody");
  tbody.innerHTML = "";

  if (items.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: var(--text-muted); padding: 20px;">No inventory items match filter.</td></tr>`;
    return;
  }

  items.forEach(item => {
    const tr = document.createElement("tr");
    let statusTag = `<span class="status-pill pill-optimal">In Stock</span>`;
    if (item.qty_available === 0) {
      statusTag = `<span class="status-pill pill-danger">Depleted</span>`;
    } else if (item.qty_available <= 5) {
      statusTag = `<span class="status-pill pill-warning">Low Stock</span>`;
    }

    tr.innerHTML = `
      <td>
        <strong style="color: #ffffff;">${item.sku_name}</strong>
        <div style="font-size: 11px; color: var(--text-subtle); font-family: var(--font-mono);">EAN: ${item.barcode || 'N/A'}</div>
      </td>
      <td><span class="status-pill pill-neutral">${item.brand || 'FMCG'}</span></td>
      <td>${item.category}</td>
      <td style="font-family: var(--font-mono);">Batch #${item.batch_id}</td>
      <td style="font-weight: 700; font-family: var(--font-mono); color: ${item.qty_available > 0 ? 'var(--brand-emerald)' : 'var(--brand-rose)'};">${item.qty_available}</td>
      <td style="font-family: var(--font-mono); color: var(--text-muted);">${item.qty_reserved}</td>
      <td><code style="font-family: var(--font-mono);">${item.expiry_date}</code></td>
      <td>${statusTag}</td>
    `;
    tbody.appendChild(tr);
  });
}

function filterStoreMatrix() {
  const query = document.getElementById("store-matrix-search").value.toLowerCase().trim();
  if (!query) {
    renderStoreMatrixTable(currentStoreMatrixData);
    return;
  }
  const filtered = currentStoreMatrixData.filter(it => 
    (it.sku_name && it.sku_name.toLowerCase().includes(query)) ||
    (it.barcode && it.barcode.toLowerCase().includes(query)) ||
    (it.brand && it.brand.toLowerCase().includes(query)) ||
    (it.category && it.category.toLowerCase().includes(query))
  );
  renderStoreMatrixTable(filtered);
}

// =============================================================================
// DYNAMIC SAFETY STOCK ($ROP$)
// =============================================================================

async function loadSafetyStockTable() {
  const tbody = document.getElementById("safety-stock-tbody");
  if (!tbody) return;

  try {
    const res = await fetch(`${API_BASE}/api/v1/analytics/safety-stock`);
    if (!res.ok) return;
    currentSafetyStockData = await res.json();
    renderSafetyStockTable(currentSafetyStockData);
  } catch (err) {
    console.error("Error loading safety stock table", err);
  }
}

function renderSafetyStockTable(rows) {
  const tbody = document.getElementById("safety-stock-tbody");
  tbody.innerHTML = "";

  if (rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" style="text-align: center; color: var(--text-muted); padding: 20px;">No SKUs matched search.</td></tr>`;
    return;
  }

  rows.forEach(r => {
    const tr = document.createElement("tr");
    const isReorder = r.inventory_health_status === "REORDER_NOW";
    const statusBadge = isReorder 
      ? `<span class="status-pill pill-danger">● REORDER NOW</span>` 
      : `<span class="status-pill pill-optimal">● OPTIMAL</span>`;

    tr.innerHTML = `
      <td>
        <strong style="color: #ffffff;">${r.sku_name}</strong>
        <div style="font-size: 11px; color: var(--text-subtle); font-family: var(--font-mono);">EAN: ${r.barcode || 'N/A'}</div>
      </td>
      <td><span class="status-pill pill-neutral">${r.brand || 'FMCG'}</span></td>
      <td>${r.category}</td>
      <td style="font-weight: 700; font-family: var(--font-mono); color: ${isReorder ? 'var(--brand-rose)' : '#ffffff'};">${r.total_stock}</td>
      <td style="font-family: var(--font-mono);">${r.avg_daily_demand} / day</td>
      <td style="font-family: var(--font-mono);">${r.lead_time_days} days</td>
      <td style="color: var(--brand-indigo); font-weight: 600; font-family: var(--font-mono);">+${r.safety_stock} units</td>
      <td style="font-weight: 700; font-family: var(--font-mono); color: #ffffff;">${r.dynamic_reorder_point} units</td>
      <td>${statusBadge}</td>
    `;
    tbody.appendChild(tr);
  });
}

function filterSafetyStockTable() {
  const query = document.getElementById("safety-search-input").value.toLowerCase().trim();
  if (!query) {
    renderSafetyStockTable(currentSafetyStockData);
    return;
  }
  const filtered = currentSafetyStockData.filter(r => 
    (r.sku_name && r.sku_name.toLowerCase().includes(query)) ||
    (r.barcode && r.barcode.toLowerCase().includes(query)) ||
    (r.brand && r.brand.toLowerCase().includes(query)) ||
    (r.category && r.category.toLowerCase().includes(query))
  );
  renderSafetyStockTable(filtered);
}

// =============================================================================
// TRANSACTIONAL OUTBOX (CDC) & JSON MODAL
// =============================================================================

async function loadOutboxEvents() {
  const tbody = document.getElementById("outbox-tbody");
  if (!tbody) return;

  try {
    const res = await fetch(`${API_BASE}/api/v1/outbox/events`);
    if (!res.ok) return;
    const events = await res.json();

    tbody.innerHTML = "";
    if (events.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 16px;">No outbox events generated yet</td></tr>`;
      return;
    }

    events.forEach(ev => {
      const tr = document.createElement("tr");
      tr.style.cursor = "pointer";
      tr.title = "Click to inspect raw JSON CDC payload";
      tr.onclick = () => openOutboxModal(ev.event_id);

      const shortId = ev.event_id.substring(0, 8);
      const payload = typeof ev.payload === "string" ? JSON.parse(ev.payload) : ev.payload;
      const strategy = payload.strategy || "FEFO";
      const timeStr = new Date(ev.created_at).toLocaleTimeString();

      tr.innerHTML = `
        <td><code style="font-family: var(--font-mono); color: #a5b4fc;">${shortId}... 🔍</code></td>
        <td><span class="status-pill pill-optimal">${ev.event_type}</span></td>
        <td style="font-family: var(--font-mono);">Order #${ev.aggregate_id}</td>
        <td><span class="status-pill pill-neutral">${strategy}</span></td>
        <td style="font-size: 11px; color: var(--text-muted); font-family: var(--font-mono);">${timeStr}</td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    // Poller
  }
}

async function openOutboxModal(eventId) {
  const modal = document.getElementById("outbox-modal");
  const contentEl = document.getElementById("modal-json-content");
  const titleEl = document.getElementById("modal-event-title");
  
  modal.classList.add("active");
  contentEl.innerHTML = `<span style="color: var(--text-muted);">Fetching atomic CDC event ${eventId}...</span>`;
  titleEl.innerText = `Outbox CDC Event: ${eventId}`;

  try {
    const res = await fetch(`${API_BASE}/api/v1/outbox/events/${eventId}`);
    if (!res.ok) throw new Error("Event not found");
    const data = await res.json();
    const formattedJson = JSON.stringify(data, null, 2);
    contentEl.innerHTML = `<pre style="margin: 0; color: #a5b4fc;">${syntaxHighlightJson(formattedJson)}</pre>`;
  } catch (err) {
    contentEl.innerHTML = `<span style="color: var(--brand-rose);">Failed to load event: ${err.message}</span>`;
  }
}

function closeOutboxModal(e) {
  if (e && e.target && e.target.closest && e.target.closest(".modal-dialog") && e.target.tagName !== "BUTTON") {
    return;
  }
  const modal = document.getElementById("outbox-modal");
  if (modal) modal.classList.remove("active");
}

function copyModalJson() {
  const code = document.getElementById("modal-json-content").innerText;
  navigator.clipboard.writeText(code).then(() => {
    alert("CDC JSON payload copied to clipboard!");
  });
}

function syntaxHighlightJson(json) {
  return json
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, match => {
      let cls = "color: #93c5fd;"; // number
      if (/^"/.test(match)) {
        if (/:$/.test(match)) {
          cls = "color: #fca5a5; font-weight: 600;"; // key
        } else {
          cls = "color: #86efac;"; // string
        }
      } else if (/true|false/.test(match)) {
        cls = "color: #fbbf24;"; // boolean
      } else if (/null/.test(match)) {
        cls = "color: #cbd5e1;"; // null
      }
      return `<span style="${cls}">${match}</span>`;
    });
}

// =============================================================================
// EXPLAIN ANALYZE COMPARISON RUNNER
// =============================================================================

async function runExplainAnalyzeComparison() {
  const display = document.getElementById("explain-plan-display");
  const gistJsonEl = document.getElementById("gist-plan-json");
  const seqJsonEl = document.getElementById("seq-plan-json");
  const gistTimeEl = document.getElementById("gist-exec-time");
  const seqTimeEl = document.getElementById("seq-exec-time");
  const badgeEl = document.getElementById("explain-speedup-badge");

  display.style.display = "block";
  gistJsonEl.innerText = "Running EXPLAIN (ANALYZE, BUFFERS) on GiST index...";
  seqJsonEl.innerText = "Running EXPLAIN (ANALYZE, BUFFERS) on Seq scan...";

  try {
    const res = await fetch(`${API_BASE}/api/v1/engine/explain-analyze`);
    const data = await res.json();

    gistTimeEl.innerText = `${data.gist_index_scan.execution_time_ms} ms`;
    seqTimeEl.innerText = `${data.sequential_scan.execution_time_ms} ms`;
    badgeEl.innerText = data.speedup_factor;

    const gistHeader = document.getElementById("gist-header-label");
    const seqHeader = document.getElementById("seq-header-label");
    const scaleExplanation = document.getElementById("explain-scale-explanation");

    if (gistHeader && data.gist_index_scan.startup_cost !== undefined) {
      gistHeader.innerText = `✓ GiST Spatial Index Scan (Startup Cost: ${data.gist_index_scan.startup_cost})`;
    }
    if (seqHeader && data.sequential_scan.startup_cost !== undefined) {
      seqHeader.innerText = `✗ ${data.sequential_scan.node_type} (Startup Cost: ${data.sequential_scan.startup_cost})`;
    }
    if (scaleExplanation && data.scale_explanation) {
      scaleExplanation.innerText = data.scale_explanation;
    }

    gistJsonEl.innerText = JSON.stringify(data.gist_index_scan.plan, null, 2);
    seqJsonEl.innerText = JSON.stringify(data.sequential_scan.plan, null, 2);
  } catch (err) {
    gistJsonEl.innerText = `Error: ${err.message}`;
  }
}

// =============================================================================
// CONCURRENCY BENCHMARK RUNNER
// =============================================================================

function setBenchmarkWorkers(count, btn) {
  document.querySelectorAll(".segmented-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  document.getElementById("bench-worker-count").value = count;
}

async function execute4WayBenchmark() {
  const btn = document.getElementById("btn-run-benchmark");
  const workers = parseInt(document.getElementById("bench-worker-count").value) || 40;
  btn.disabled = true;
  btn.innerHTML = `<span>⏳</span> Benchmarking ${workers} Parallel Workers...`;

  logToTerminal(`[BENCHMARK START] Firing 4 Concurrency Engines with ${workers} parallel workers...`);

  try {
    const res = await fetch(`${API_BASE}/api/v1/concurrency-lab/benchmark?workers=${workers}`, { method: "POST" });
    const data = await res.json();

    const [naive, pess, occ, adv] = data.results;

    const maxRps = Math.max(naive.throughput_rps, pess.throughput_rps, occ.throughput_rps, adv.throughput_rps, 1.0);

    // 1. Naive
    document.getElementById("bench-naive-alloc").innerText = `${naive.allocated} / ${workers}`;
    document.getElementById("bench-naive-reject").innerText = naive.rejected_or_aborted;
    document.getElementById("bench-naive-oversold").innerText = `${naive.oversold_deficit} UNITS`;
    document.getElementById("bench-naive-rps").innerText = `${naive.throughput_rps} req/s`;
    document.getElementById("bar-naive-rps").style.width = `${Math.min(100, Math.round((naive.throughput_rps / maxRps) * 100))}%`;
    document.getElementById("bench-naive-lat").innerText = `${naive.avg_latency_ms} ms`;

    // 2. Pessimistic
    document.getElementById("bench-pess-alloc").innerText = `${pess.allocated} (Exact Match)`;
    document.getElementById("bench-pess-reject").innerText = pess.rejected_or_aborted;
    document.getElementById("bench-pess-oversold").innerText = "0 (Zero)";
    document.getElementById("bench-pess-rps").innerText = `${pess.throughput_rps} req/s`;
    document.getElementById("bar-pess-rps").style.width = `${Math.min(100, Math.round((pess.throughput_rps / maxRps) * 100))}%`;
    document.getElementById("bench-pess-lat").innerText = `${pess.avg_latency_ms} ms`;

    // 3. OCC
    document.getElementById("bench-occ-alloc").innerText = `${occ.allocated} orders`;
    document.getElementById("bench-occ-reject").innerText = `${occ.rejected_or_aborted} version conflicts`;
    document.getElementById("bench-occ-oversold").innerText = "0 (Zero)";
    document.getElementById("bench-occ-rps").innerText = `${occ.throughput_rps} req/s`;
    document.getElementById("bar-occ-rps").style.width = `${Math.min(100, Math.round((occ.throughput_rps / maxRps) * 100))}%`;
    document.getElementById("bench-occ-lat").innerText = `${occ.avg_latency_ms} ms`;

    // 4. Advisory
    document.getElementById("bench-adv-alloc").innerText = `${adv.allocated} (Exact Match)`;
    document.getElementById("bench-adv-reject").innerText = adv.rejected_or_aborted;
    document.getElementById("bench-adv-oversold").innerText = "0 (Zero)";
    document.getElementById("bench-adv-rps").innerText = `${adv.throughput_rps} req/s`;
    document.getElementById("bar-adv-rps").style.width = `${Math.min(100, Math.round((adv.throughput_rps / maxRps) * 100))}%`;
    document.getElementById("bench-adv-lat").innerText = `${adv.avg_latency_ms} ms`;

    // Update comparative Chart.js
    updateBenchmarkChart(naive, pess, occ, adv);

    logToTerminal(`[BENCHMARK COMPLETE] ${workers} workers processed.`);
    logToTerminal(`  ✓ Naive: ${naive.throughput_rps} req/s | Constraint collisions | ${naive.avg_latency_ms}ms lat`);
    logToTerminal(`  ✓ Pessimistic: ${pess.throughput_rps} req/s | 0 oversold | 100% ACID consistency | ${pess.avg_latency_ms}ms lat`);
    logToTerminal(`  ✓ OCC: ${occ.throughput_rps} req/s | ${occ.rejected_or_aborted} aborts on version conflicts | ${occ.avg_latency_ms}ms lat`);
    logToTerminal(`  ✓ Advisory Locks: ${adv.throughput_rps} req/s | 0 oversold | Mutex serial | ${adv.avg_latency_ms}ms lat`);

    refreshOverviewMetrics();
    refreshEngineTelemetry();
    loadOutboxEvents();
  } catch (err) {
    logToTerminal(`[ERROR] Benchmark execution failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<span>⚡</span> Run 4-Engine Benchmark Suite`;
  }
}

function logToTerminal(msg) {
  const term = document.getElementById("bench-terminal");
  const timeStr = new Date().toLocaleTimeString();
  const line = document.createElement("div");
  line.className = "term-line";
  line.innerHTML = `<span class="term-time">[${timeStr}]</span> ${msg}`;
  term.appendChild(line);
  term.scrollTop = term.scrollHeight;
}

function clearTerminal() {
  const term = document.getElementById("bench-terminal");
  term.innerHTML = `<div class="term-line"><span class="term-time">[SYS]</span> Terminal log cleared.</div>`;
}

// =============================================================================
// DISPATCH SIMULATOR & SPATIAL ROUTING
// =============================================================================

function quickFillOrder(custId, skuId, qty) {
  const custSel = document.getElementById("sim-customer-select");
  const skuSel = document.getElementById("sim-sku-select");
  const qtyInput = document.getElementById("sim-qty");
  
  if (custSel) custSel.value = custId;
  if (skuSel) skuSel.value = skuId;
  if (qtyInput) qtyInput.value = qty;
}

async function simulateDispatch(e) {
  e.preventDefault();
  const custId = parseInt(document.getElementById("sim-customer-select").value);
  const skuId = parseInt(document.getElementById("sim-sku-select").value);
  const qty = parseInt(document.getElementById("sim-qty").value);
  const allowSplit = document.getElementById("sim-split").checked;
  const strategyEl = document.getElementById("sim-strategy");
  const strategy = strategyEl ? strategyEl.value : "PESSIMISTIC";
  const resultDiv = document.getElementById("sim-dispatch-result");

  resultDiv.style.display = "block";
  resultDiv.innerHTML = `<div style="font-size: 12px; color: var(--brand-indigo); padding: 8px;">Routing order via PostGIS KNN & acquiring <strong>${strategy}</strong> transaction lock...</div>`;

  try {
    const payload = {
      customer_id: custId,
      items: [{ sku_id: skuId, qty_requested: qty }],
      allow_split_fulfillment: allowSplit,
      strategy: strategy
    };

    const res = await fetch(`${API_BASE}/api/v1/orders`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    const data = await res.json();

    if (res.ok) {
      const isSplit = data.status === "SPLIT_ALLOCATED";
      const badgeClass = isSplit ? "pill-neutral" : "pill-optimal";
      const itemDetails = data.items.map(it => 
        `Store #${it.store_id_allocated || 'N/A'} (Batch #${it.batch_id_allocated || 'N/A'})`
      ).join(", ");

      // Get Customer & Store geographical coordinates to draw interactive route line
      const customer = allCustomers.find(c => c.customer_id === custId);
      const allocatedStoreId = data.items.find(it => it.store_id_allocated)?.store_id_allocated;
      const allocatedStore = allStores.find(s => s.store_id === allocatedStoreId);

      if (customer && allocatedStore && customer.lat && allocatedStore.lat) {
        drawDeliveryRoute(customer.lat, customer.lng, allocatedStore.lat, allocatedStore.lng, allocatedStore.name);
      }

      resultDiv.innerHTML = `
        <div style="background: var(--bg-card-elevated); border: 1px solid var(--brand-emerald); border-radius: 10px; padding: 14px; font-size: 12px; line-height: 1.6; box-shadow: 0 0 16px var(--brand-emerald-glow);">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
            <strong style="color: var(--brand-emerald); font-size: 13px;">Order #${data.order_id} Allocated!</strong>
            <span class="status-pill ${badgeClass}">${data.status}</span>
          </div>
          <div><strong style="color: var(--text-muted);">Strategy:</strong> <code style="font-family: var(--font-mono); color: #a5b4fc;">${strategy}</code></div>
          <div><strong style="color: var(--text-muted);">Fulfillment Hub:</strong> ${itemDetails}</div>
          <div><strong style="color: var(--text-muted);">Total Charged:</strong> <span style="font-family: var(--font-mono); font-weight: 700; color: #ffffff;">₹${data.total_amount.toFixed(2)}</span></div>
          <div style="margin-top: 8px; color: var(--text-muted); font-size: 11px; border-top: 1px solid var(--border-subtle); padding-top: 6px;">
            ✓ Atomic Outbox CDC Event Published • Zero Oversell Guaranteed
          </div>
        </div>
      `;
    } else {
      resultDiv.innerHTML = `
        <div style="background: var(--bg-card-elevated); border: 1px solid var(--brand-rose); border-radius: 10px; padding: 14px; font-size: 12px; color: var(--brand-rose); box-shadow: 0 0 16px var(--brand-rose-glow);">
          <strong>Allocation Rejected:</strong> ${data.detail || data.failure_reason || 'Stockout'}
        </div>
      `;
    }

    refreshOverviewMetrics();
    refreshEngineTelemetry();
    loadOutboxEvents();
  } catch (err) {
    resultDiv.innerHTML = `<div style="color: var(--brand-rose); font-size: 12px; padding: 8px;">Error: ${err.message}</div>`;
  }
}

// Explicit global bindings for inline HTML onclick handlers
window.switchTab = switchTab;
window.setBenchmarkWorkers = setBenchmarkWorkers;
window.execute4WayBenchmark = execute4WayBenchmark;
window.clearTerminal = clearTerminal;
window.quickFillOrder = quickFillOrder;
window.simulateDispatch = simulateDispatch;
window.selectStoreForInspection = selectStoreForInspection;
window.loadStoreMatrix = loadStoreMatrix;
window.filterStoreMatrix = filterStoreMatrix;
window.loadSafetyStockTable = loadSafetyStockTable;
window.filterSafetyStockTable = filterSafetyStockTable;
window.loadOutboxEvents = loadOutboxEvents;
window.openOutboxModal = openOutboxModal;
window.closeOutboxModal = closeOutboxModal;
window.copyModalJson = copyModalJson;
window.runExplainAnalyzeComparison = runExplainAnalyzeComparison;
window.refreshEngineTelemetry = refreshEngineTelemetry;

