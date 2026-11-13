// so101-lab Web-App – Statusseite. Reines JavaScript, keine Bibliotheken, kein Build-Schritt.
//
// Ablauf:
//  1. /api/info fragt, ob ein Token nötig ist. Das Token kommt aus ?token=… oder einem Dialog
//     und bleibt nur in diesem Tab gespeichert (sessionStorage).
//  2. Der WebSocket /ws liefert eine client_id und danach 20-mal je Sekunde den Zustand aller
//     verbundenen Geräte. Der Browser schickt alle 100 ms ein Lebenszeichen (Heartbeat).
//  3. Aktionen gehen per REST an /api/… und tragen die client_id. Nur wer steuert, darf bewegen.
"use strict";

const HEARTBEAT_MS = 100;
const $ = (id) => document.getElementById(id);

const app = {
  token: null,
  clientId: null,
  ws: null,
  devices: [],
  ports: [],
  snapshots: {},
};

// ------------------------------------------------------------------ Token

function loadToken() {
  const url = new URL(location.href);
  const fromUrl = url.searchParams.get("token");
  if (fromUrl) {
    sessionStorage.setItem("so101-token", fromUrl);
    url.searchParams.delete("token");
    history.replaceState(null, "", url); // Token nicht in der Adresszeile stehen lassen
  }
  app.token = sessionStorage.getItem("so101-token");
}

function askToken() {
  return new Promise((resolve) => {
    const dialog = $("token-dialog");
    $("token-form").onsubmit = () => {
      app.token = $("token-input").value.trim();
      sessionStorage.setItem("so101-token", app.token);
      resolve();
    };
    dialog.showModal();
  });
}

// ------------------------------------------------------------------ REST

async function api(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  if (app.token) headers.Authorization = `Bearer ${app.token}`;
  const response = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (response.status === 401) {
    sessionStorage.removeItem("so101-token");
    await askToken();
    reconnectLive();
    return api(method, path, body);
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Fehler ${response.status}`);
  return data;
}

function toast(text, kind = "error") {
  const el = $("toast");
  el.textContent = text;
  el.className = `show ${kind}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.className = ""), 5000);
}

async function action(fn) {
  try {
    await fn();
  } catch (err) {
    toast(err.message);
  }
}

// ------------------------------------------------------------------ Geräte und Ports

function selectedDevice() {
  return app.devices.find((d) => d.name === $("device-select").value);
}

async function loadDevices() {
  app.devices = await api("GET", "/api/devices");
  const select = $("device-select");
  const keep = select.value || localStorage.getItem("so101-device");
  select.innerHTML = "";
  for (const d of app.devices) {
    const option = new Option(`${d.name} – ${d.type}, ${d.backend}${d.connected ? " (verbunden)" : ""}`, d.name);
    select.add(option);
  }
  if (keep && app.devices.some((d) => d.name === keep)) select.value = keep;
  updatePortRow();
  render();
}

async function loadPorts() {
  app.ports = await api("GET", "/api/ports");
  const select = $("port-select");
  const keep = select.value;
  select.innerHTML = "";
  if (app.ports.length === 0) {
    select.add(new Option("kein USB-Seriell-Port gefunden", ""));
  }
  for (const p of app.ports) select.add(new Option(p.label, p.device));
  const configured = selectedDevice()?.port;
  if (keep && app.ports.some((p) => p.device === keep)) select.value = keep;
  else if (configured && app.ports.some((p) => p.device === configured)) select.value = configured;
  updatePortRow();
}

function updatePortRow() {
  const device = selectedDevice();
  const usb = device?.backend === "usb";
  $("port-label").style.display = usb ? "" : "none";
  $("port-refresh").style.display = usb ? "" : "none";
  $("port-hint").textContent = usb
    ? "Alle USB- und ACM-Ports dieses Rechners. Bei mehreren Adaptern hilft die Seriennummer [SN …]."
    : device
      ? `Backend '${device.backend}': kein Port nötig.`
      : "";
}

// ------------------------------------------------------------------ Live-Verbindung

function openLive() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const query = app.token ? `?token=${encodeURIComponent(app.token)}` : "";
  const ws = new WebSocket(`${scheme}://${location.host}/ws${query}`);
  app.ws = ws;
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "hello") {
      app.clientId = msg.client_id;
      setLive(true);
    } else if (msg.type === "state") {
      const before = Object.keys(app.snapshots).sort().join();
      app.snapshots = msg.devices;
      if (Object.keys(app.snapshots).sort().join() !== before) loadDevices().catch(() => {});
      render();
    }
  };
  ws.onclose = (event) => {
    app.clientId = null;
    setLive(false);
    if (app.ws !== ws) return; // absichtlich ersetzt
    if (event.code === 4401) {
      askToken().then(reconnectLive);
      return;
    }
    setTimeout(openLive, 1000);
  };
}

function reconnectLive() {
  const old = app.ws;
  app.ws = null;
  if (old) old.close();
  openLive();
}

function setLive(ok) {
  const el = $("live-status");
  el.textContent = ok ? "live" : "getrennt";
  el.className = `live ${ok ? "ok" : "bad"}`;
  render();
}

setInterval(() => {
  if (app.ws && app.ws.readyState === WebSocket.OPEN) app.ws.send('{"type":"heartbeat"}');
}, HEARTBEAT_MS);

// ------------------------------------------------------------------ Anzeige

const fmt = (v, digits = 0) => (v === null || v === undefined ? "–" : Number(v).toFixed(digits));

function render() {
  const device = selectedDevice();
  const snap = device ? app.snapshots[device.name] : null;
  const connected = Boolean(snap);
  const mine = connected && snap.controller && snap.controller === app.clientId;
  const other = connected && snap.controller && !mine;

  $("connect").disabled = !device || connected;
  $("disconnect").disabled = !connected || other;
  $("device-select").disabled = false;
  $("port-select").disabled = connected;
  $("take-control").disabled = !connected || mine || other || !app.clientId;
  $("force-control").disabled = !connected || !other || !app.clientId;
  $("release-control").disabled = !mine;
  $("torque-on").disabled = !mine;
  $("torque-off").disabled = !mine;

  const status = $("control-status");
  status.className = mine ? "mine" : other ? "other" : "";
  status.textContent = !connected
    ? "Kein Gerät verbunden."
    : mine
      ? `Du steuerst. Watchdog ${snap.watchdog_ms} ms, sicherer Zustand: ${snap.safe_state}.`
      : other
        ? "Ein anderer Client steuert. Du beobachtest."
        : "Niemand steuert. Du beobachtest.";

  renderFacts(snap);
  renderJoints(snap);
  renderEvents(snap);
}

function renderFacts(snap) {
  const facts = $("facts");
  if (!snap) {
    facts.innerHTML = "";
    return;
  }
  const rows = [
    ["Gerät", `${snap.name} (${snap.description})`],
    ["Backend", snap.port ? `${snap.backend}, ${snap.port}` : snap.backend],
    [
      "Kalibrierung",
      snap.calibration_path
        ? `${snap.calibration_path}${snap.calibrated ? "" : " (unvollständig)"}`
        : `keine Datei für '${snap.calibration_id}' – Grenzen 0…4095`,
    ],
    ["Sicherer Zustand", snap.safe_state === "hold" ? "Stellung halten (hold)" : "Drehmoment aus (torque_off)"],
    ["Abfrage", `${fmt(snap.cycle_hz, 1)} Hz`],
  ];
  if (snap.missing.length) rows.push(["Keine Antwort", snap.missing.join(", ")]);
  if (snap.error) rows.push(["Fehler", snap.error]);
  facts.innerHTML = "";
  for (const [k, v] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = k;
    const dd = document.createElement("dd");
    dd.textContent = v;
    facts.append(dt, dd);
  }
}

function renderJoints(snap) {
  const body = $("joints").querySelector("tbody");
  body.innerHTML = "";
  for (const j of snap?.joints ?? []) {
    const tr = document.createElement("tr");
    const cells = [
      j.name,
      j.id,
      j.position,
      j.value === null ? "–" : `${fmt(j.value, 1)} ${j.unit}`,
      j.mode === "position" ? `${j.limits[0]}…${j.limits[1]}` : "Rad",
      fmt(j.velocity),
      fmt(j.load),
      fmt(j.voltage, 1),
      fmt(j.temperature),
      fmt(j.current_ma),
      j.torque === null ? "–" : j.torque ? "an" : "aus",
      j.errors.join(", ") || "–",
    ];
    cells.forEach((text, i) => {
      const td = document.createElement("td");
      td.textContent = text;
      if (i === 10 && j.torque) td.className = "on";
      if (i === 11 && j.errors.length) td.className = "err";
      tr.append(td);
    });
    body.append(tr);
  }
}

function renderEvents(snap) {
  const list = $("events");
  const events = snap?.events ?? [];
  const key = events.map((e) => e.id).join();
  if (list.dataset.key === key) return;
  list.dataset.key = key;
  list.innerHTML = "";
  for (const e of [...events].reverse()) {
    const li = document.createElement("li");
    li.className = e.level;
    const time = document.createElement("time");
    time.textContent = new Date(e.time * 1000).toLocaleTimeString("de-DE");
    li.append(time, e.text);
    list.append(li);
  }
}

// ------------------------------------------------------------------ Bedienung

function deviceUrl(suffix) {
  return `/api/devices/${encodeURIComponent($("device-select").value)}/${suffix}`;
}

async function estop() {
  try {
    const result = await api("POST", "/api/estop");
    toast(result.stopped.length ? `NOT-AUS ausgelöst: ${result.stopped.join(", ")}` : "NOT-AUS: kein Gerät verbunden", "info");
  } catch (err) {
    toast(`NOT-AUS fehlgeschlagen: ${err.message}. Netzteil ausschalten!`);
  }
}

function bind() {
  $("estop").onclick = estop;
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") estop(); // Esc = Not-Aus
  });
  $("device-select").onchange = () => {
    localStorage.setItem("so101-device", $("device-select").value);
    updatePortRow();
    loadPorts().catch(() => {});
    render();
  };
  $("port-refresh").onclick = () => action(loadPorts);
  $("connect").onclick = () =>
    action(async () => {
      const usb = selectedDevice()?.backend === "usb";
      const port = usb ? $("port-select").value || null : null;
      if (usb && !port) throw new Error("Bitte einen Port wählen.");
      await api("POST", deviceUrl("connect"), { port });
      await loadDevices();
    });
  $("disconnect").onclick = () =>
    action(async () => {
      await api("POST", deviceUrl("disconnect"), { client_id: app.clientId });
      await loadDevices();
    });
  $("take-control").onclick = () => action(() => api("POST", deviceUrl("control"), { client_id: app.clientId }));
  $("force-control").onclick = () =>
    action(async () => {
      if (!confirm("Ein anderer Client steuert gerade. Wirklich übernehmen?")) return;
      await api("POST", deviceUrl("control"), { client_id: app.clientId, force: true });
    });
  $("release-control").onclick = () =>
    action(() => api("POST", deviceUrl("control/release"), { client_id: app.clientId }));
  $("torque-on").onclick = () =>
    action(() => api("POST", deviceUrl("torque"), { client_id: app.clientId, enabled: true }));
  $("torque-off").onclick = () =>
    action(() => api("POST", deviceUrl("torque"), { client_id: app.clientId, enabled: false }));
}

async function main() {
  loadToken();
  bind();
  const info = await (await fetch("/api/info")).json();
  if (!info.docs) {
    $("docs-link").classList.add("disabled");
    $("docs-link").title = "Doku wurde nicht gebaut (so101-lab web ohne --no-docs starten)";
  }
  if (info.auth_required && !app.token) await askToken();
  await action(loadDevices);
  await action(loadPorts);
  openLive();
}

main();
