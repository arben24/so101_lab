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
  setup: null, // Einrichtungsassistent (vom Server, für alle Tabs sichtbar)
  setupTypes: [],
  mockPort: "mock",
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

// Rückfrage als Dialog in der Seite. NICHT window.confirm(): Das hält das ganze JavaScript an,
// auch das Lebenszeichen an den Server. Nach 400 ms würde der Watchdog das Steuerrecht entziehen.
function ask(text, okLabel = "Ja, fortfahren") {
  return new Promise((resolve) => {
    const dialog = $("ask-dialog");
    $("ask-text").textContent = text;
    $("ask-ok").textContent = okLabel;
    const finish = (answer) => {
      dialog.onclose = null;
      dialog.close();
      resolve(answer);
    };
    $("ask-ok").onclick = () => finish(true);
    $("ask-cancel").onclick = () => finish(false);
    dialog.onclose = () => resolve(false); // Esc schließt den Dialog: gilt als Nein
    dialog.showModal();
  });
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
  fillSetupPorts();
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
      app.setup = msg.setup ?? null;
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
  renderServo(snap, mine);
  renderRegisterControls(snap, mine);
  renderCalibration(snap, mine);
  renderSetup();
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
      if (!(await ask("Ein anderer Client steuert gerade. Wirklich übernehmen?", "Übernehmen"))) return;
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
  bindTabs();
  bindServo();
  bindRegisters();
  bindCalibration();
  bindSetup();
  const info = await (await fetch("/api/info")).json();
  if (!info.docs) {
    $("docs-link").classList.add("disabled");
    $("docs-link").title = "Doku wurde nicht gebaut (so101-lab web ohne --no-docs starten)";
  }
  if (info.auth_required && !app.token) await askToken();
  await action(loadDevices);
  await action(loadSetupTypes);
  await action(loadPorts);
  openLive();
}

// ------------------------------------------------------------------ Reiter

const TABS = ["status", "servo", "register", "kalibrierung", "einrichtung"];

function showTab() {
  const tab = TABS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "status";
  for (const t of TABS) $(`tab-${t}`).classList.toggle("active", t === tab);
  for (const a of $("tabs").querySelectorAll("a")) a.classList.toggle("active", a.hash === `#${tab}`);
}

function bindTabs() {
  window.addEventListener("hashchange", showTab);
  showTab();
}

function currentSnap() {
  const device = selectedDevice();
  return device ? app.snapshots[device.name] : null;
}

function fillJointSelect(select, joints) {
  const names = joints.map((j) => j.name);
  if (select.dataset.names === names.join()) return;
  const keep = select.value;
  select.innerHTML = "";
  for (const n of names) select.add(new Option(n, n));
  if (names.includes(keep)) select.value = keep;
  select.dataset.names = names.join();
}

// ------------------------------------------------------------------ Einzelservo

const servo = { dragging: false, pending: null, timer: null, lastSent: null };

function servoJoint(snap) {
  return snap?.joints?.find((j) => j.name === $("servo-joint").value);
}

function renderServo(snap, mine) {
  const joints = snap?.joints ?? [];
  fillJointSelect($("servo-joint"), joints);
  const j = servoJoint(snap);
  const position = j && j.mode === "position";
  for (const id of ["servo-motion", "servo-torque-on", "servo-torque-off"]) $(id).disabled = !mine;
  $("servo-slider").disabled = !mine || !position;
  if (snap && document.activeElement !== $("servo-velocity") && document.activeElement !== $("servo-accel")) {
    $("servo-velocity").value = snap.motion.velocity;
    $("servo-accel").value = snap.motion.acceleration;
  }
  const gauges = $("servo-gauges");
  gauges.innerHTML = "";
  if (!j) return;
  const items = [
    ["Roh", j.position],
    ["Wert", j.value === null ? "–" : `${fmt(j.value, 1)} ${j.unit}`],
    ["Tempo", fmt(j.velocity)],
    ["Last (0,1 %)", fmt(j.load)],
    ["Strom mA", fmt(j.current_ma)],
    ["Spannung V", fmt(j.voltage, 1)],
    ["Temperatur °C", fmt(j.temperature)],
    ["Drehmoment", j.torque === null ? "–" : j.torque ? "an" : "aus"],
    ["Fehler", j.errors.join(", ") || "–"],
  ];
  for (const [label, value] of items) {
    const div = document.createElement("div");
    div.className = "gauge";
    const span = document.createElement("span");
    span.textContent = label;
    const b = document.createElement("b");
    b.textContent = value;
    div.append(span, b);
    gauges.append(div);
  }
  const slider = $("servo-slider");
  slider.min = j.limits[0];
  slider.max = j.limits[1];
  $("servo-min").textContent = j.limits[0];
  $("servo-max").textContent = j.limits[1];
  if (!servo.dragging) slider.value = j.position;
  $("servo-pos").textContent = j.position;
  $("servo-hint").textContent = !position
    ? "Räder werden mit der LeKiwi-Fahrsteuerung bewegt, nicht hier."
    : !snap.calibrated
      ? "Keine Kalibrierung: Grenzen sind 0…4095, das Gelenk kann gegen einen Anschlag fahren. Erst kalibrieren!"
      : !j.torque
        ? "Drehmoment ist aus: Der Schieberegler setzt das Ziel, bewegen tut sich erst mit Drehmoment an."
        : `Jeder Sollwert wird auf ${j.limits[0]}…${j.limits[1]} und auf den größten Sprung je Befehl begrenzt.`;
}

function sendServoGoal() {
  servo.timer = null;
  if (servo.pending === null) return;
  const name = $("servo-joint").value;
  const value = Number(servo.pending);
  servo.pending = null;
  action(async () => {
    const result = await api("POST", deviceUrl("move"), { client_id: app.clientId, targets: { [name]: value } });
    $("servo-goal").textContent = result.sent[name] + (result.sent[name] !== value ? ` (begrenzt von ${value})` : "");
  });
}

function bindServo() {
  const slider = $("servo-slider");
  slider.addEventListener("pointerdown", () => (servo.dragging = true));
  slider.addEventListener("pointerup", () => (servo.dragging = false));
  slider.addEventListener("input", () => {
    servo.pending = slider.value;
    if (!servo.timer) servo.timer = setTimeout(sendServoGoal, 60); // höchstens ~16 Sollwerte je Sekunde
  });
  slider.addEventListener("change", () => {
    servo.pending = slider.value;
    servo.dragging = false;
    if (!servo.timer) sendServoGoal();
  });
  $("servo-joint").onchange = () => {
    $("servo-goal").textContent = "–";
    render();
  };
  $("servo-motion").onclick = () =>
    action(() =>
      api("POST", deviceUrl("motion"), {
        client_id: app.clientId,
        velocity: Number($("servo-velocity").value),
        acceleration: Number($("servo-accel").value),
      }),
    );
  $("servo-torque-on").onclick = () =>
    action(() =>
      api("POST", deviceUrl("torque"), { client_id: app.clientId, enabled: true, joints: [$("servo-joint").value] }),
    );
  $("servo-torque-off").onclick = () =>
    action(() =>
      api("POST", deviceUrl("torque"), { client_id: app.clientId, enabled: false, joints: [$("servo-joint").value] }),
    );
}

// ------------------------------------------------------------------ Register-Explorer

function renderRegisterControls(snap, mine) {
  fillJointSelect($("reg-joint"), snap?.joints ?? []);
  $("reg-read").disabled = !snap;
  for (const b of $("reg-table").querySelectorAll("button")) b.disabled = !mine;
}

async function readRegisters() {
  const joint = $("reg-joint").value;
  const rows = await api("GET", deviceUrl(`registers/${encodeURIComponent(joint)}`));
  const body = $("reg-table").querySelector("tbody");
  body.innerHTML = "";
  const expert = $("reg-expert").checked;
  for (const r of rows) {
    const tr = document.createElement("tr");
    const cells = [r.address, r.name, r.area, r.value ?? "–", r.unit || "–", r.text];
    for (const text of cells) {
      const td = document.createElement("td");
      td.textContent = text;
      tr.append(td);
    }
    if (r.area === "EEPROM") tr.children[2].className = "eeprom";
    const td = document.createElement("td");
    if (r.write === "ram" || (r.write === "eeprom" && expert)) {
      const input = document.createElement("input");
      input.type = "number";
      input.value = r.value ?? "";
      const button = document.createElement("button");
      button.textContent = "Schreiben";
      if (r.write === "eeprom") button.className = "warn";
      button.onclick = () => writeRegister(joint, r, Number(input.value));
      td.append(input, " ", button);
    } else {
      td.className = "reason";
      td.textContent = r.write === "eeprom" ? "EEPROM: Expertenmodus einschalten" : r.reason;
    }
    tr.append(td);
    body.append(tr);
  }
  render();
}

function writeRegister(joint, r, value) {
  action(async () => {
    const eeprom = r.write === "eeprom";
    if (eeprom) {
      const ok = await ask(
        `EEPROM schreiben: ${joint}.${r.name} = ${r.value} → ${value}\n\n` +
          "Der Wert bleibt dauerhaft im Servo, auch nach dem Ausschalten. Das Drehmoment dieses Servos wird " +
          "dabei ausgeschaltet. Falsche Werte können das Verhalten des Servos stark verändern.\n\nWirklich schreiben?",
        "EEPROM schreiben",
      );
      if (!ok) return;
    }
    const result = await api("POST", deviceUrl("registers"), {
      client_id: app.clientId,
      joint,
      register_name: r.name,
      value,
      confirm_eeprom: eeprom,
    });
    toast(`${joint}.${r.name} = ${result.value} (zurückgelesen)`, "info");
    await readRegisters();
  });
}

function bindRegisters() {
  $("reg-read").onclick = () => action(readRegisters);
  $("reg-expert").onchange = () => {
    if ($("reg-table").querySelector("tbody").children.length) action(readRegisters);
  };
}

// ------------------------------------------------------------------ Kalibrierung

function renderCalibration(snap, mine) {
  const cal = snap?.calibration;
  const stage = cal?.stage ?? "start";
  const running = stage === "mitte" || stage === "bereich";
  const facts = $("cal-facts");
  facts.innerHTML = "";
  if (snap) {
    const rows = [
      ["Gerät", `${snap.name} (${snap.description}), Kalibrier-ID '${snap.calibration_id}'`],
      ["Aktuelle Datei", snap.calibration_path ?? "keine – Grenzen 0…4095"],
      ["Wird gespeichert nach", cal?.target_path ?? snap.calibration_target],
    ];
    if ((cal?.target_exists || snap.calibration_path === snap.calibration_target) && stage !== "fertig")
      rows.push(["Hinweis", "Eine vorhandene Datei wird ersetzt, die alte bleibt als .json.bak erhalten."]);
    if (cal?.saved_path) rows.push(["Gespeichert", cal.saved_path]);
    if (!mine) rows.push(["Steuerrecht", "Zum Kalibrieren erst „Steuerung übernehmen“."]);
    for (const [k, v] of rows) {
      const dt = document.createElement("dt");
      dt.textContent = k;
      const dd = document.createElement("dd");
      dd.textContent = v;
      facts.append(dt, dd);
    }
  }
  const order = ["start", "mitte", "bereich", "fertig"];
  const index = order.indexOf(stage === "abgebrochen" ? "start" : stage);
  const ids = ["cal-step-start", "cal-step-middle", "cal-step-range", "cal-step-done"];
  ids.forEach((id, i) => {
    $(id).className = i === index ? "current" : i < index ? "done" : "";
  });
  $("cal-start").disabled = !mine || running;
  $("cal-middle").disabled = !mine || stage !== "mitte";
  $("cal-finish").disabled = !mine || stage !== "bereich";
  $("cal-cancel").disabled = !mine || !running;

  const body = $("cal-table").querySelector("tbody");
  body.innerHTML = "";
  for (const j of cal?.joints ?? []) {
    const tr = document.createElement("tr");
    const cells = [j.name, j.offset ?? "–", j.recorded ? (j.min ?? "–") : "0", j.position ?? "–", j.recorded ? (j.max ?? "–") : "4095"];
    for (const text of cells) {
      const td = document.createElement("td");
      td.textContent = text;
      tr.append(td);
    }
    const td = document.createElement("td");
    const bar = document.createElement("div");
    bar.className = "range-bar";
    const lo = j.recorded ? j.min : 0;
    const hi = j.recorded ? j.max : 4095;
    if (lo !== null && hi !== null) {
      const span = document.createElement("div");
      span.className = "span";
      span.style.left = `${(lo / 4095) * 100}%`;
      span.style.width = `${Math.max(0.5, ((hi - lo) / 4095) * 100)}%`;
      bar.append(span);
    }
    if (j.position !== null) {
      const now = document.createElement("div");
      now.className = "now";
      now.style.left = `${(Math.min(4095, Math.max(0, j.position)) / 4095) * 100}%`;
      bar.append(now);
    }
    td.append(bar);
    tr.append(td);
    body.append(tr);
  }
}

function calibrationStep(step) {
  return action(() => api("POST", deviceUrl(`calibration/${step}`), { client_id: app.clientId }));
}

function bindCalibration() {
  $("cal-start").onclick = async () => {
    const ok = await ask(
      "Kalibrierung starten? Das Drehmoment aller Gelenke geht aus, der Arm wird kraftlos. Arm festhalten!",
      "Starten",
    );
    if (ok) calibrationStep("start");
  };
  $("cal-middle").onclick = () => calibrationStep("middle");
  $("cal-finish").onclick = () => calibrationStep("finish");
  $("cal-cancel").onclick = () => calibrationStep("cancel");
}

// ------------------------------------------------------------------ Einrichtung (Servo-IDs)

async function loadSetupTypes() {
  const data = await api("GET", "/api/setup/types");
  app.setupTypes = data.types;
  app.mockPort = data.mock_port;
  const select = $("setup-type");
  const keep = select.value || localStorage.getItem("so101-setup-type");
  select.innerHTML = "";
  for (const t of app.setupTypes) select.add(new Option(`${t.type} – ${t.description}`, t.type));
  if (keep && app.setupTypes.some((t) => t.type === keep)) select.value = keep;
  render();
}

function fillSetupPorts() {
  const select = $("setup-port");
  const keep = select.value;
  select.innerHTML = "";
  for (const p of app.ports) select.add(new Option(p.label, p.device));
  select.add(new Option("Simulation: ein neuer Servo (ohne Hardware)", app.mockPort));
  if (keep && [...select.options].some((o) => o.value === keep)) select.value = keep;
}

function setupSingle(s) {
  return s?.found?.length === 1 ? s.found[0] : null;
}

function setupStepIndex(s) {
  if (!s) return -1;
  if (s.found !== null) return setupSingle(s) ? 2 : 1;
  if (s.last?.ok) return s.last.action === "neustart" ? 5 : 4;
  return 1;
}

// Ziel der ID-Vergabe: ein Gelenk des Gerätetyps oder eine freie ID (0–253). null = ungültig.
function setupTarget(s) {
  if (!s) return null;
  if ($("setup-mode").value === "free") {
    const raw = $("setup-free-id").value.trim();
    const id = Number(raw);
    if (raw === "" || !Number.isInteger(id) || id < 0 || id > 253) return null;
    const joint = s.order.find((j) => j.id === id);
    return { id, joint: joint?.name ?? null, body: { id } };
  }
  const joint = s.order.find((j) => j.name === $("setup-joint").value);
  return joint ? { id: joint.id, joint: joint.name, body: { joint: joint.name } } : null;
}

function setupPlan(s, target) {
  const servo = setupSingle(s);
  if (!servo || !target) return [];
  const steps = [];
  if (servo.id !== target.id) steps.push(`ID ${servo.id} → ${target.id}${target.joint ? ` (${target.joint})` : ""}`);
  if (servo.baudrate !== 1000000) steps.push(`Baudrate ${servo.baudrate} → 1000000`);
  return steps;
}

function setFacts(el, rows) {
  el.innerHTML = "";
  for (const [k, v] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = k;
    const dd = document.createElement("dd");
    dd.textContent = v;
    el.append(dt, dd);
  }
}

function renderSetup() {
  const s = app.setup;
  const mine = Boolean(s && app.clientId && s.owner === app.clientId);
  const busy = Boolean(s?.busy);
  const can = mine && !busy;
  $("setup-type").disabled = Boolean(s);
  $("setup-port").disabled = Boolean(s);
  $("setup-port-refresh").disabled = Boolean(s);
  $("setup-start").disabled = Boolean(s) || !app.clientId;
  $("setup-stop").disabled = !can;

  const rows = [];
  if (!s) {
    rows.push(["Stand", "Nicht gestartet. Gerätetyp und Port wählen, dann „Assistent starten“."]);
  } else {
    rows.push(["Gerätetyp", `${s.device_type} (${s.description})`]);
    rows.push(["Port", s.simulated ? "Simulation (keine Hardware)" : `${s.port}${s.adapter_sn ? ` [SN ${s.adapter_sn}]` : ""}`]);
    rows.push(["Protokoll", s.log_path]);
    rows.push(["Zustand", s.busy ? `${s.busy} …` : "bereit"]);
    if (!mine) rows.push(["Hinweis", "Ein anderer Browser-Tab benutzt den Assistenten. Du beobachtest."]);
  }
  setFacts($("setup-facts"), rows);

  // Reihenfolge: vom Server, vor dem Start aus dem gewählten Gerätetyp
  const type = app.setupTypes.find((t) => t.type === $("setup-type").value);
  const order = s ? s.order : (type?.joints ?? []).map((j) => ({ ...j, status: "–" }));
  const body = $("setup-order").querySelector("tbody");
  const key = JSON.stringify([order, s?.next]);
  if (body.dataset.key !== key) {
    body.dataset.key = key;
    body.innerHTML = "";
    for (const j of order) {
      const tr = document.createElement("tr");
      if (s && j.name === s.next) tr.className = "next";
      for (const [text, cls] of [[j.name, ""], [j.id, ""], [j.status, j.status]]) {
        const td = document.createElement("td");
        td.textContent = text;
        if (cls) td.className = cls;
        tr.append(td);
      }
      body.append(tr);
    }
  }

  // Gelenkauswahl: bei jedem neuen „nächsten“ Gelenk dieses vorschlagen
  const jointSelect = $("setup-joint");
  const names = (s?.order ?? []).map((j) => `${j.name}|${j.id}`).join();
  if (jointSelect.dataset.names !== names) {
    jointSelect.dataset.names = names;
    jointSelect.innerHTML = "";
    for (const j of s?.order ?? []) jointSelect.add(new Option(`${j.name} (ID ${j.id})`, j.name));
  }
  if (s && s.next && jointSelect.dataset.next !== s.next) {
    jointSelect.dataset.next = s.next;
    jointSelect.value = s.next;
  }

  $("setup-sim-row").style.display = s?.simulated ? "" : "none";
  $("setup-sim-plug").disabled = !can;
  $("setup-scan").disabled = !can;
  $("setup-scan").textContent = s?.busy === "Suche läuft" ? "Suche läuft (ca. 8 s) …" : "Servo suchen";
  jointSelect.disabled = !mine;

  const found = $("setup-found");
  const servo = setupSingle(s);
  found.className = s?.found && !servo ? "bad" : "";
  found.textContent = !s || s.found === null
    ? ""
    : s.found.length === 0
      ? "Nichts gefunden. Servo-Netzteil an? Kabel gesteckt? Richtiger Port?"
      : servo
        ? `Gefunden: ID ${servo.id}, ${servo.model_name}, ${servo.baudrate} Baud, Firmware ${servo.firmware ?? "?"}.`
        : `Es antworten ${s.found.length} Servos (IDs ${s.found.map((f) => f.id).join(", ")}). Nur EINEN anschließen und neu suchen!`;

  const free = $("setup-mode").value === "free";
  $("setup-joint-label").style.display = free ? "none" : "";
  $("setup-free-label").style.display = free ? "" : "none";
  $("setup-mode").disabled = !mine;
  $("setup-free-id").disabled = !mine;
  const target = setupTarget(s);
  const plan = setupPlan(s, target);
  // Wie der Server: den eben fertig eingerichteten Servo nicht auf ein anderes Ziel umstellen
  const finished = servo && s.order.find((j) => j.id === servo.id && ["gesetzt", "dauerhaft"].includes(j.status));
  const blocked = Boolean(finished && target && finished.id !== target.id);
  $("setup-plan").className = blocked || (servo && !target) ? "bad" : "";
  $("setup-plan").textContent = !servo
    ? ""
    : !target
      ? "Bitte eine ID von 0 bis 253 eingeben."
      : blocked
        ? `Das ist der eben eingerichtete Servo ${finished.name} (ID ${servo.id}). Erst den nächsten Servo anschließen und neu suchen.`
        : plan.length
          ? `Geplant: ${plan.join(", ")}.`
          : "Der Servo hat schon diese ID und 1 Mbit/s. „Nur prüfen“ liest zurück und sucht.";
  $("setup-assign").disabled = !can || !servo || !target || blocked;
  $("setup-assign").textContent = servo && !plan.length ? "Nur prüfen" : "ID schreiben";
  $("setup-verify").disabled = !can || !s?.last;

  const checks = $("setup-checks");
  const last = s?.last;
  const checksKey = JSON.stringify(last);
  if (checks.dataset.key !== checksKey) {
    checks.dataset.key = checksKey;
    checks.innerHTML = "";
    if (last) {
      const head = document.createElement("li");
      head.className = last.ok ? "ok" : "bad";
      head.textContent = `${last.joint || `ID ${last.new_id}`}: ${last.message}`;
      checks.append(head);
      for (const c of last.checks) {
        const li = document.createElement("li");
        li.className = c.ok ? "ok" : "bad";
        li.textContent = c.text;
        checks.append(li);
      }
    }
  }

  const index = setupStepIndex(s);
  ["setup-step-plug", "setup-step-scan", "setup-step-assign", "setup-step-result", "setup-step-restart", "setup-step-next"].forEach(
    (id, i) => ($(id).className = i === index ? "current" : i < index ? "done" : ""),
  );

  const list = $("setup-events");
  const events = s?.events ?? [];
  const eventsKey = events.map((e) => e.id).join();
  if (list.dataset.key !== eventsKey) {
    list.dataset.key = eventsKey;
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
}

async function setupCall(path, body = {}) {
  app.setup = await api("POST", `/api/setup/${path}`, { client_id: app.clientId, ...body });
  render();
  return app.setup;
}

function bindSetup() {
  $("setup-type").onchange = () => {
    localStorage.setItem("so101-setup-type", $("setup-type").value);
    render();
  };
  $("setup-port-refresh").onclick = () => action(loadPorts);
  $("setup-start").onclick = () =>
    action(async () => {
      const port = $("setup-port").value;
      if (!port) throw new Error("Bitte einen Port wählen.");
      await setupCall("start", { port, device_type: $("setup-type").value });
    });
  $("setup-stop").onclick = () =>
    action(async () => {
      await api("POST", "/api/setup/stop", { client_id: app.clientId });
      app.setup = null;
      render();
    });
  $("setup-sim-plug").onclick = () => action(() => setupCall("simulate", { variant: $("setup-sim-variant").value }));
  $("setup-scan").onclick = () => action(() => setupCall("scan"));
  $("setup-joint").onchange = () => render();
  $("setup-mode").onchange = () => render();
  $("setup-free-id").oninput = () => render();
  $("setup-assign").onclick = () =>
    action(async () => {
      const target = setupTarget(app.setup);
      if (!target) throw new Error("Bitte eine ID von 0 bis 253 eingeben.");
      const plan = setupPlan(app.setup, target);
      if (plan.length) {
        const ok = await ask(
          `${plan.join("\n")}\n\nDas wird dauerhaft in den Servo geschrieben (EEPROM). ` +
            "Am Bus darf nur dieser eine Servo hängen. Wirklich schreiben?",
          "Schreiben",
        );
        if (!ok) return;
      }
      const result = await setupCall("assign", target.body);
      toast(result.last.message, result.last.ok ? "info" : "error");
    });
  $("setup-verify").onclick = () =>
    action(async () => {
      const result = await setupCall("verify", { id: app.setup.last.new_id });
      toast(result.last.message, result.last.ok ? "info" : "error");
    });
}

main();
