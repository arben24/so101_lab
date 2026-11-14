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
  renderMotion(snap, mine);
  renderCalFiles(snap, mine);
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
  bindMotion();
  bindCalFiles();
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

const TABS = ["status", "servo", "register", "kalibrierung", "kalibrierdateien", "aufnahmen", "einrichtung"];

function showTab() {
  const tab = TABS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "status";
  for (const t of TABS) $(`tab-${t}`).classList.toggle("active", t === tab);
  for (const a of $("tabs").querySelectorAll("a")) a.classList.toggle("active", a.hash === `#${tab}`);
  if (tab === "aufnahmen") action(loadLibrary);
  if (tab === "kalibrierdateien") action(loadCalFiles);
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

// ------------------------------------------------------------------ Aufnahmen

const lib = { items: [], lastSaved: null };

async function loadLibrary() {
  lib.items = await api("GET", "/api/trajectories");
  renderLibrary();
  render();
}

function td(text) {
  const cell = document.createElement("td");
  cell.textContent = text;
  return cell;
}

function renderLibrary() {
  const body = $("lib-table").querySelector("tbody");
  body.innerHTML = "";
  if (!lib.items.length) {
    const tr = document.createElement("tr");
    const cell = td("Noch keine Aufnahmen.");
    cell.colSpan = 8;
    tr.append(cell);
    body.append(tr);
    return;
  }
  for (const t of lib.items) {
    const tr = document.createElement("tr");
    tr.dataset.name = t.name;
    const created = t.created ? new Date(t.created) : new Date(t.modified * 1000);
    const source = (t.source ?? (t.recorded_with ? "" : "Skript 06")) + (t.interrupted ? ` – unterbrochen: ${t.interrupted}` : "");
    tr.append(
      td(t.name),
      td(`${t.device ?? "?"} (${t.device_type ?? "?"})`),
      td(source || "–"),
      td(`${fmt(t.duration_s, 1)} s`),
      td(t.samples ?? "–"),
      td(t.measured_rate_hz ? `${fmt(t.measured_rate_hz, 1)} Hz` : "–"),
      td(created.toLocaleString("de-DE")),
    );
    const actions = document.createElement("td");
    const view = document.createElement("button");
    view.textContent = "Ansehen";
    view.className = "secondary";
    view.onclick = () => action(() => showTrajectory(t.name));
    const play = document.createElement("button");
    play.textContent = "Abspielen";
    play.className = "warn play";
    play.onclick = () => action(() => playTrajectory(t));
    const csv = document.createElement("a");
    csv.textContent = "CSV";
    csv.href = `/api/trajectories/${encodeURIComponent(t.name)}/csv${app.token ? `?token=${encodeURIComponent(app.token)}` : ""}`;
    csv.download = `${t.name}.csv`;
    const del = document.createElement("button");
    del.textContent = "Löschen";
    del.className = "secondary del";
    del.onclick = () => action(() => deleteTrajectory(t.name));
    actions.append(view, play, del, csv);
    tr.append(actions);
    body.append(tr);
  }
}

async function showTrajectory(name) {
  const d = await api("GET", `/api/trajectories/${encodeURIComponent(name)}`);
  for (const tr of $("lib-table").querySelectorAll("tbody tr")) tr.classList.toggle("selected", tr.dataset.name === name);
  const W = 800, H = 260, left = 44, right = 8, top = 8, bottom = 22;
  let lo = Infinity, hi = -Infinity;
  for (const row of d.positions) for (const v of row) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  if (hi - lo < 20) { lo -= 10; hi += 10; }
  const tMax = d.times[d.times.length - 1] || 1;
  const x = (t) => left + (t / tMax) * (W - left - right);
  const y = (v) => top + (1 - (v - lo) / (hi - lo)) * (H - top - bottom);
  let svg = "";
  for (const v of [lo, (lo + hi) / 2, hi]) {
    svg += `<line class="grid" x1="${left}" x2="${W - right}" y1="${y(v)}" y2="${y(v)}"/>`;
    svg += `<text x="4" y="${y(v) + 4}">${Math.round(v)}</text>`;
  }
  svg += `<text x="${left}" y="${H - 6}">0 s</text><text x="${W - right - 40}" y="${H - 6}">${tMax.toFixed(1)} s</text>`;
  d.joints.forEach((joint, j) => {
    const points = d.times.map((t, i) => `${x(t).toFixed(1)},${y(d.positions[i][j]).toFixed(1)}`).join(" ");
    svg += `<polyline points="${points}" style="stroke: var(--c${(j % 6) + 1})"/>`;
  });
  $("lib-plot").innerHTML = svg;
  const legend = $("lib-legend");
  legend.innerHTML = "";
  d.joints.forEach((joint, j) => {
    const span = document.createElement("span");
    span.style.setProperty("--swatch", `var(--c${(j % 6) + 1})`);
    span.textContent = joint;
    legend.append(span);
  });
  const conv = d.has_calibration ? "mit Kalibrierung gespeichert" : "ohne gespeicherte Kalibrierung";
  $("lib-plot-title").textContent = `${d.name}: Rohwerte (Schritte) über der Zeit, ${d.joints.length} Gelenke, ${conv}`;
  $("lib-plot-wrap").classList.add("show");
}

async function playTrajectory(t) {
  const snap = currentSnap();
  if (!snap) throw new Error("Erst oben ein Gerät verbinden und die Steuerung übernehmen.");
  const speed = Number($("play-speed").value);
  // Erst prüfen lassen (bewegt nichts): Anfahrt, Dauer, Umrechnung, Hinweise wie „begrenzt“
  const check = await api(
    "GET",
    deviceUrl(`playback/check?trajectory=${encodeURIComponent(t.name)}&speed=${encodeURIComponent(speed)}`),
  );
  const notes = check.notes.length ? `\n\nHinweise:\n${check.notes.map((n) => `• ${n}`).join("\n")}` : "";
  const ok = await ask(
    `'${t.name}' auf '${snap.name}' abspielen (Tempo ×${speed})?\n\n` +
      `Erst fährt der Arm in ${check.approach_s} s sanft zur Startpose, dann läuft die Aufnahme ${check.play_s} s. ` +
      "Danach hält er die Endstellung (Drehmoment an, bis „Steuerung abgeben“). " +
      `Umrechnung: ${check.conversion}.${notes}\n\nArbeitsbereich frei, Hand am Not-Aus (Esc)?`,
    "Abspielen",
  );
  if (!ok) return;
  await api("POST", deviceUrl("playback/start"), { client_id: app.clientId, trajectory: t.name, speed });
}

async function deleteTrajectory(name) {
  if (!(await ask(`Aufnahme '${name}' (CSV und JSON) endgültig löschen?`, "Löschen"))) return;
  await api("DELETE", `/api/trajectories/${encodeURIComponent(name)}?client_id=${encodeURIComponent(app.clientId ?? "")}`);
  $("lib-plot-wrap").classList.remove("show");
  await loadLibrary();
}

function renderMotion(snap, mine) {
  const warn = $("rec-calwarn");
  const uncalibrated = Boolean(snap && !snap.calibration_path);
  warn.hidden = !uncalibrated;
  if (uncalibrated && warn.dataset.device !== snap.name) {
    warn.dataset.device = snap.name;
    warn.innerHTML = "";
    warn.append(
      `Keine Kalibrierdatei für '${snap.name}' (ID '${snap.calibration_id}'). Aufnehmen und Abspielen gehen trotzdem, ` +
        "aber die Grenzen sind 0…4095: Ein Gelenk kann gegen einen Anschlag fahren. Empfohlen: ",
    );
    const link = document.createElement("a");
    link.href = "#kalibrierung";
    link.textContent = "erst kalibrieren";
    const files = document.createElement("a");
    files.href = "#kalibrierdateien";
    files.textContent = "Kalibrierdateien ansehen";
    warn.append(link, " oder ", files, ".");
  }
  if (!uncalibrated) warn.dataset.device = "";
  const rec = snap?.recording;
  const play = snap?.playback;
  const tele = snap?.teleop;
  $("rec-start").disabled = !mine || Boolean(rec) || Boolean(play);
  $("rec-stop").disabled = !mine || !rec;
  const recStatus = $("rec-status");
  recStatus.className = rec ? "status-line live" : "status-line";
  const last = snap?.last_recording;
  recStatus.textContent = rec
    ? `● Aufnahme '${rec.name}' läuft: ${fmt(rec.elapsed_s, 1)} s, ${rec.samples} Abtastungen – ${snap.recording_source}`
    : last
      ? last.saved
        ? `Zuletzt gespeichert: ${last.path}${last.interrupted ? ` (unterbrochen: ${last.interrupted})` : ""}`
        : `Zuletzt: '${last.name}' nicht gespeichert${last.error ? `: ${last.error}` : " (zu kurz)"}`
      : "";
  const lastKey = JSON.stringify(last ?? null);
  if (lib.lastSaved !== lastKey) {
    const first = lib.lastSaved === null;
    lib.lastSaved = lastKey;
    if (!first && last?.saved) action(loadLibrary);
  }

  // Leader: alle anderen verbundenen Geräte
  const select = $("tele-leader");
  const others = Object.keys(app.snapshots).filter((n) => n !== snap?.name);
  if (select.dataset.names !== others.join()) {
    const keep = select.value;
    select.innerHTML = "";
    if (!others.length) select.add(new Option("kein anderes Gerät verbunden", ""));
    for (const n of others) select.add(new Option(`${n} (${app.snapshots[n].type})`, n));
    if (others.includes(keep)) select.value = keep;
    select.dataset.names = others.join();
  }
  $("tele-start").disabled = !mine || Boolean(tele) || Boolean(play) || !others.length;
  $("tele-stop").disabled = !mine || !tele;
  $("tele-status").className = tele ? "status-line live" : "status-line";
  $("tele-status").textContent = tele
    ? `Folgt '${tele.leader}': ${tele.phase === "anfahrt" ? "sanfte Anfahrt …" : "folgt"} (${tele.conversion})`
    : "";

  $("play-stop").disabled = !mine || !play;
  $("play-progress").firstElementChild.style.width = play ? `${Math.min(100, (play.elapsed_s / play.total_s) * 100)}%` : "0";
  $("play-status").className = play ? "status-line live" : "status-line";
  $("play-status").textContent = play
    ? `'${play.name}' ×${play.speed}: ${play.phase === "anfahrt" ? `sanfte Anfahrt (${play.approach_s} s)` : "spielt ab"}, ${fmt(play.elapsed_s, 1)} von ${fmt(play.total_s, 1)} s`
    : snap && !mine
      ? "Zum Abspielen, Aufnehmen oder Folgen erst „Steuerung übernehmen“."
      : "";
  for (const b of $("lib-table").querySelectorAll("button.play")) b.disabled = !mine || Boolean(play) || Boolean(tele) || Boolean(rec);
  for (const b of $("lib-table").querySelectorAll("button.del")) b.disabled = !app.clientId;
}

function bindMotion() {
  $("lib-refresh").onclick = () => action(loadLibrary);
  $("rec-start").onclick = () =>
    action(async () => {
      const name = $("rec-name").value.trim();
      if (!name) throw new Error("Bitte einen Namen für die Aufnahme eingeben.");
      const snap = currentSnap();
      if (!snap?.teleop) {
        const ok = await ask(
          `Aufnahme '${name}' auf '${snap.name}' starten?\n\nDas Drehmoment geht aus, der Arm wird kraftlos. ` +
            "Einen Follower festhalten, er sackt sonst ab. Dann von Hand führen.",
          "Starten",
        );
        if (!ok) return;
      }
      await api("POST", deviceUrl("recording/start"), {
        client_id: app.clientId,
        name,
        overwrite: $("rec-overwrite").checked,
      });
    });
  $("rec-stop").onclick = () =>
    action(async () => {
      const result = await api("POST", deviceUrl("recording/stop"), { client_id: app.clientId });
      toast(result?.saved ? `Gespeichert: ${result.path}` : "Nicht gespeichert (zu kurz).", result?.saved ? "info" : "error");
      await loadLibrary();
    });
  $("tele-start").onclick = () =>
    action(async () => {
      const leader = $("tele-leader").value;
      const snap = currentSnap();
      const ok = await ask(
        `'${snap.name}' folgt ab jetzt '${leader}'.\n\nDas Drehmoment von '${snap.name}' geht an. Er fährt erst in ` +
          "mindestens 2 s sanft zur Stellung des Leaders und folgt ihm dann. Arbeitsbereich frei?",
        "Folgen",
      );
      if (!ok) return;
      await api("POST", deviceUrl("teleop/start"), { client_id: app.clientId, leader });
    });
  $("tele-stop").onclick = () => action(() => api("POST", deviceUrl("teleop/stop"), { client_id: app.clientId }));
  $("play-stop").onclick = () => action(() => api("POST", deviceUrl("playback/stop"), { client_id: app.clientId }));
}

// ------------------------------------------------------------------ Kalibrierdateien

const calf = { items: [], shown: null, calPath: undefined };

function calUrl(item, suffix = "") {
  return `/api/calibrations/${item.source}/${item.kind}/${item.name}/${encodeURIComponent(item.id)}${suffix}`;
}

async function loadCalFiles() {
  calf.items = await api("GET", "/api/calibrations");
  const body = $("calf-table").querySelector("tbody");
  body.innerHTML = "";
  if (!calf.items.length) {
    const tr = document.createElement("tr");
    const cell = td("Keine Kalibrierdateien gefunden.");
    cell.colSpan = 7;
    tr.append(cell);
    body.append(tr);
  }
  for (const item of calf.items) {
    const tr = document.createElement("tr");
    tr.dataset.key = `${item.source}/${item.kind}/${item.name}/${item.id}`;
    const state = td(
      !item.exists ? "gelöscht (Backup vorhanden)" : item.problems.length ? `⚠ ${item.problems.join(" ")}` : "in Ordnung",
    );
    state.className = !item.exists ? "gone" : item.problems.length ? "bad" : "ok";
    tr.append(
      td(item.source === "repo" ? "Repo" : "LeRobot"),
      td(`${item.device_type} (${item.kind}/${item.name})`),
      td(item.id + (item.has_backup && item.exists ? " (+ Backup)" : "")),
      td(item.used_by.join(", ") || "–"),
      td(new Date(item.modified * 1000).toLocaleString("de-DE")),
      state,
    );
    const actions = document.createElement("td");
    const button = (label, cls, fn) => {
      const b = document.createElement("button");
      b.textContent = label;
      b.className = cls;
      b.onclick = () => action(fn);
      actions.append(b);
      return b;
    };
    if (item.exists) button("Ansehen", "secondary", () => showCalFile(item, false));
    if (item.has_backup) button("Backup ansehen", "secondary", () => showCalFile(item, true));
    if (item.source === "repo" && item.exists) button("Löschen", "secondary needs-live", () => deleteCalFile(item));
    if (item.source === "repo" && item.has_backup) button("Wiederherstellen", "secondary needs-live", () => restoreCalFile(item));
    if (item.source === "lerobot" && item.exists) button("Ins Repo kopieren", "secondary needs-live", () => copyCalFile(item));
    if (item.exists) {
      const link = document.createElement("a");
      link.textContent = "JSON";
      link.href = calUrl(item, "/json") + (app.token ? `?token=${encodeURIComponent(app.token)}` : "");
      link.download = `${item.id}.json`;
      actions.append(link);
    }
    tr.append(actions);
    body.append(tr);
  }
  render();
}

async function showCalFile(item, backup) {
  const d = await api("GET", calUrl(item) + (backup ? "?backup=true" : ""));
  calf.shown = { ...item, backup };
  for (const tr of $("calf-table").querySelectorAll("tbody tr"))
    tr.classList.toggle("selected", tr.dataset.key === `${item.source}/${item.kind}/${item.name}/${item.id}`);
  $("calf-detail").hidden = false;
  $("calf-title").textContent = `${d.id}${backup ? " (Backup)" : ""}: ${d.device_type}${d.used_by.length ? `, benutzt von ${d.used_by.join(", ")}` : ""}`;
  $("calf-path").textContent = d.path;
  const problems = $("calf-problems");
  problems.innerHTML = "";
  for (const text of d.problems) {
    const li = document.createElement("li");
    li.className = "bad";
    li.textContent = text;
    problems.append(li);
  }
  const body = $("calf-joints").querySelector("tbody");
  body.innerHTML = "";
  for (const j of d.joints) {
    const tr = document.createElement("tr");
    tr.append(td(j.name), td(j.id), td(j.drive_mode), td(j.homing_offset), td(j.range_min), td(j.range_max));
    body.append(tr);
  }
}

async function deleteCalFile(item) {
  const users = item.used_by.length ? `\n\nBenutzt von: ${item.used_by.join(", ")}. Diese Geräte haben danach keine Kalibrierung mehr (Grenzen 0…4095).` : "";
  if (!(await ask(`Kalibrierdatei '${item.id}' löschen?\n\nSie wird zu ${item.id}.json.bak und lässt sich wiederherstellen. Ein älteres Backup wird ersetzt.${users}`, "Löschen"))) return;
  await api("POST", calUrl(item, "/delete"), { client_id: app.clientId });
  $("calf-detail").hidden = true;
  await loadCalFiles();
}

async function restoreCalFile(item) {
  const text = item.exists
    ? `Datei und Backup von '${item.id}' tauschen? Die aktuelle Datei wird zum Backup.`
    : `Gelöschte Kalibrierdatei '${item.id}' aus dem Backup wiederherstellen?`;
  if (!(await ask(`${text}\n\nDanach mit „Vergleichen“ prüfen, ob die Servos zur Datei passen.`, "Wiederherstellen"))) return;
  await api("POST", calUrl(item, "/restore"), { client_id: app.clientId });
  await loadCalFiles();
  toast("Wiederhergestellt. Jetzt „Vergleichen“: Passen die Servos zur Datei?", "info");
}

async function copyCalFile(item) {
  const exists = calf.items.some((i) => i.source === "repo" && i.kind === item.kind && i.name === item.name && i.id === item.id && i.exists);
  if (exists && !(await ask(`Im Repo gibt es '${item.id}' schon. Ersetzen (die alte wird Backup)?`, "Ersetzen"))) return;
  await api("POST", calUrl(item, "/copy"), { client_id: app.clientId, overwrite: exists });
  await loadCalFiles();
}

function renderCompare(result) {
  const body = $("calf-cmp").querySelector("tbody");
  body.innerHTML = "";
  for (const j of result.joints) {
    const tr = document.createElement("tr");
    const f = j.file ?? {};
    const pairs = [
      [f.homing_offset, j.servo.homing_offset],
      [f.min_position_limit, j.servo.min_position_limit],
      [f.max_position_limit, j.servo.max_position_limit],
    ];
    tr.append(td(j.name));
    for (const [a, b] of pairs) {
      tr.append(td(a ?? "–"));
      const cell = td(b ?? "keine Antwort");
      if (j.file && a !== b) cell.className = "bad";
      tr.append(cell);
    }
    const ok = td(j.file ? (j.match ? "✔" : "✘") : "keine Datei");
    ok.className = j.match ? "ok" : "bad";
    tr.append(ok);
    body.append(tr);
  }
  const status = $("calf-cmp-status");
  status.className = result.all_match ? "status-line" : "status-line live";
  status.textContent = !result.calibration_path
    ? "Für dieses Gerät ist keine Kalibrierdatei geladen."
    : result.all_match
      ? `Servos und Datei stimmen überein (${result.calibration_path}).`
      : `Servos und Datei weichen ab (${result.calibration_path}). Rohwerte und Grenzen passen dann nicht zusammen.`;
}

function renderCalFiles(snap, mine) {
  $("calf-compare").disabled = !snap;
  $("calf-reload").disabled = !snap || !app.clientId;
  $("calf-apply").disabled = !mine || !snap?.calibration_path;
  $("calf-import").disabled = !app.clientId;
  for (const b of $("calf-table").querySelectorAll("button.needs-live")) b.disabled = !app.clientId;
  // Gerät gewechselt oder Datei neu geladen: alten Vergleich nicht stehen lassen
  const key = snap ? `${snap.name}|${snap.calibration_path}` : "";
  if (calf.calPath !== key) {
    calf.calPath = key;
    $("calf-cmp").querySelector("tbody").innerHTML = "";
    $("calf-cmp-status").textContent = snap
      ? `Gerät '${snap.name}', Datei: ${snap.calibration_path ?? "keine"}. „Vergleichen“ liest die Servos.`
      : "Oben ein Gerät verbinden, um Datei und Servos zu vergleichen.";
    $("calf-cmp-status").className = "status-line";
  }
  const select = $("calf-type");
  if (!select.options.length && app.setupTypes.length) {
    for (const t of app.setupTypes) select.add(new Option(`${t.type} – ${t.description}`, t.type));
  }
}

function bindCalFiles() {
  $("calf-refresh").onclick = () => action(loadCalFiles);
  $("calf-compare").onclick = () => action(async () => renderCompare(await api("GET", deviceUrl("calibration/compare"))));
  $("calf-reload").onclick = () =>
    action(async () => {
      await api("POST", deviceUrl("calibration/reload"), { client_id: app.clientId });
      await loadCalFiles();
    });
  $("calf-apply").onclick = () =>
    action(async () => {
      const snap = currentSnap();
      const ok = await ask(
        `Werte aus ${snap.calibration_path} in die Servos von '${snap.name}' schreiben?\n\n` +
          "Homing-Offset und Positionsgrenzen werden dauerhaft ins EEPROM geschrieben (nur geänderte). Das " +
          "Drehmoment der betroffenen Servos geht dabei aus: Arm festhalten. Nur sinnvoll, wenn die Datei " +
          "wirklich zu diesem Arm gehört.",
        "Schreiben",
      );
      if (!ok) return;
      const result = await api("POST", deviceUrl("calibration/apply"), { client_id: app.clientId });
      renderCompare(result);
      toast(result.written.length ? `${result.written.length} Werte geschrieben.` : "Stimmte schon überein.", "info");
    });
  $("calf-import").onclick = () =>
    action(async () => {
      const file = $("calf-file").files[0];
      const calId = $("calf-id").value.trim() || (file ? file.name.replace(/\.json$/i, "") : "");
      if (!file) throw new Error("Bitte eine JSON-Datei wählen.");
      let content;
      try {
        content = JSON.parse(await file.text());
      } catch {
        throw new Error(`${file.name} ist kein gültiges JSON.`);
      }
      const result = await api("POST", "/api/calibrations/import", {
        client_id: app.clientId,
        device_type: $("calf-type").value,
        cal_id: calId,
        content,
        overwrite: $("calf-overwrite").checked,
      });
      toast(`Importiert: ${result.path}`, "info");
      await loadCalFiles();
    });
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
