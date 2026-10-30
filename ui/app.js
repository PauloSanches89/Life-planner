let screen = null;
let task = null;
let selectedId = null;
let retainedId = null;
let focusTarget = null;
let notices = [];
let chain = Promise.resolve();
let polling = false;
let detailTab = "details";
let chatMessages = [];

function api() {
  return {
    snapshot: (id) => rpc("snapshot", id),
    set_selected_day: (iso, id) => rpc("set_selected_day", iso, id),
    create_task: () => rpc("create_task"),
    update_task: (id, title, description, start, end, ongoing, location) =>
      rpc("update_task", id, title, description, start, end, ongoing, location),
    delete_task: (id) => rpc("delete_task", id),
    add_subtask: (id, text) => rpc("add_subtask", id, text),
    update_subtask: (id, subtaskId, text) => rpc("update_subtask", id, subtaskId, text),
    delete_subtask: (id, subtaskId) => rpc("delete_subtask", id, subtaskId),
    add_link: (id, url, kind, label) => rpc("add_link", id, url, kind, label),
    delete_link: (id, linkId) => rpc("delete_link", id, linkId),
    add_alarm: (id, when) => rpc("add_alarm", id, when),
    delete_alarm: (id, alarmId) => rpc("delete_alarm", id, alarmId),
    undo: (id) => rpc("undo", id),
    redo: (id) => rpc("redo", id),
    drain_alarm_events: () => rpc("drain_alarm_events"),
    open_link: (url) => rpc("open_link", url),
    set_view_mode: (mode) => rpc("set_view_mode", mode),
  };
}

async function rpc(name, ...args) {
  const response = await fetch("/api/rpc", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, args }),
  });
  if (response.status === 401) {
    showGate(await response.json());
    return { ok: false, error: "Sign in required" };
  }
  return response.json();
}

function run(action) {
  const next = chain.then(action, action);
  chain = next.then(
    () => {},
    () => {}
  );
  return next;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function formatDay(iso) {
  const parts = iso.split("-").map(Number);
  return new Date(parts[0], parts[1] - 1, parts[2]).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function showError(message) {
  const node = document.getElementById("form-error");
  node.textContent = message || "";
  node.hidden = !message;
}

function take(result) {
  if (!result || !result.ok) {
    focusTarget = null;
    showError(result && result.error ? result.error : "Something went wrong");
    return false;
  }
  screen = result.screen;
  task = result.task;
  selectedId = task ? task.id : null;
  showError("");
  render();
  return true;
}

function render() {
  document.getElementById("undo").disabled = !screen.can_undo;
  document.getElementById("redo").disabled = !screen.can_redo;
  document.getElementById("selected-day").value = screen.selected_day;
  document.getElementById("profile-name").textContent = screen.user.display_name || "Signed in";
  const avatar = document.getElementById("avatar");
  const initial = (screen.user.display_name || "?").slice(0, 1);
  if (screen.user.avatar_url) {
    avatar.textContent = "";
    avatar.style.backgroundImage = "url(" + JSON.stringify(screen.user.avatar_url) + ")";
  } else {
    avatar.style.backgroundImage = "";
    avatar.textContent = initial;
  }
  document.getElementById("mode-summary").setAttribute("aria-pressed", screen.mode === "summary" ? "true" : "false");
  document.getElementById("mode-detailed").setAttribute("aria-pressed", screen.mode === "detailed" ? "true" : "false");
  renderCards();
  renderDetail();
  applyFocus();
}

function renderCards() {
  const list = document.getElementById("task-list");
  list.replaceChildren();
  screen.tasks.forEach((item) => {
    const card = el("div", "task-card" + (task && task.id === item.id ? " selected" : ""));
    const open = el("button", "card-open");
    open.type = "button";
    open.append(el("div", "card-title", item.title || "Untitled"));
    if (item.ongoing) {
      open.append(el("div", "card-span", "Ongoing"));
    } else if (item.start !== item.end) {
      open.append(el("div", "card-span", formatDay(item.start) + " – " + formatDay(item.end)));
    }
    if (item.location) open.append(el("div", "card-note", item.location));
    if (screen.mode === "detailed") {
      if (item.description) open.append(el("div", "card-note", item.description));
      (item.subtasks || []).forEach((subtask) => {
        if (subtask.text) open.append(el("div", "card-note", "• " + subtask.text));
      });
    }
    open.addEventListener("click", () => run(() => selectTask(item.id)));
    card.append(open);

    const bubbles = el("div", "bubbles");
    item.links.forEach((link) => {
      const bubble = el("button", "bubble " + link.kind, link.kind === "drawio" ? "io" : "↗");
      bubble.type = "button";
      bubble.title = link.label;
      bubble.setAttribute("aria-label", "Open " + link.label);
      bubble.addEventListener("click", (event) => {
        event.stopPropagation();
        run(() => openLink(link.url));
      });
      bubbles.append(bubble);
    });
    card.append(bubbles);
    list.append(card);
  });

  const count = screen.tasks.length;
  const when = screen.selected_day === screen.today ? "today" : formatDay(screen.selected_day);
  document.getElementById("task-count").textContent = "Total tasks for " + when + ": " + count;
}

function renderDetail() {
  const detail = document.getElementById("detail");
  detail.replaceChildren();
  if (!task) {
    detail.append(el("p", "empty", "Select a task or create one."));
    return;
  }

  const titleRow = el("div", "title-row");
  const title = el("input");
  title.id = "task-title";
  title.type = "text";
  title.value = task.title;
  title.setAttribute("aria-label", "Title");
  title.addEventListener("keydown", (event) => {
    if (event.key === "Enter") title.blur();
  });
  title.addEventListener("blur", () => {
    if (title.value === task.title) return;
    const id = task.id;
    const value = title.value;
    run(() => saveTitle(id, value));
  });
  const remove = el("button", null, "Delete");
  remove.id = "delete-task";
  remove.type = "button";
  remove.addEventListener("click", () => run(deleteTask));
  titleRow.append(title, remove);
  detail.append(titleRow);
  detail.append(detailTabs());
  if (detailTab === "map") {
    detail.append(mapPanel());
    return;
  }

  detail.append(section("Description", descriptionField()));
  detail.append(section("Location", locationField()));
  detail.append(section("Dates", spanFields()));
  detail.append(section("Subtasks", subtaskFields()));
  detail.append(section("Links", linkFields()));
  detail.append(section("Alarms", alarmFields()));
}

function section(heading, body) {
  const node = el("section", "section");
  node.append(el("h2", null, heading));
  if (body instanceof Node) node.append(body);
  else body.forEach((child) => node.append(child));
  return node;
}

function detailTabs() {
  const tabs = el("div", "tabs");
  tabs.append(tabButton("details", "Details"), tabButton("map", "Map"));
  return tabs;
}

function tabButton(id, label) {
  const button = el("button", "tab" + (detailTab === id ? " selected" : ""), label);
  button.type = "button";
  button.addEventListener("click", () => {
    const taskId = task.id;
    const location = document.getElementById("task-location");
    const locationValue = location ? location.value : null;
    const locationChanged = location && locationValue !== (task.location || "");
    detailTab = id;
    if (locationChanged) {
      run(() => saveLocation(taskId, locationValue));
      return;
    }
    renderDetail();
  });
  return button;
}

function locationField() {
  const field = el("input");
  field.id = "task-location";
  field.type = "text";
  field.value = task.location || "";
  field.placeholder = "Place or address";
  field.setAttribute("aria-label", "Location");
  field.addEventListener("keydown", (event) => {
    if (event.key === "Enter") field.blur();
  });
  field.addEventListener("blur", () => {
    if (field.value === (task.location || "")) return;
    const id = task.id;
    const value = field.value;
    run(() => saveLocation(id, value));
  });
  return field;
}

function mapPanel() {
  if (!task.location) return el("p", "empty", "Add a location to preview it on the map.");
  const frame = el("iframe", "map-frame");
  const query = new URLSearchParams({ q: task.location, z: "15", output: "embed" });
  frame.src = "https://maps.google.com/maps?" + query.toString();
  frame.title = "Map of " + task.location;
  frame.loading = "lazy";
  frame.referrerPolicy = "no-referrer-when-downgrade";
  frame.allowFullscreen = true;
  return frame;
}

function descriptionField() {
  const field = el("textarea");
  field.id = "task-description";
  field.value = task.description;
  field.setAttribute("aria-label", "Description");
  field.addEventListener("blur", () => {
    if (field.value === task.description) return;
    const id = task.id;
    const value = field.value;
    run(() => saveDescription(id, value));
  });
  return field;
}

function spanFields() {
  const wrap = el("div");
  const toggle = el("label", "ongoing-toggle");
  const checkbox = el("input");
  checkbox.id = "ongoing";
  checkbox.type = "checkbox";
  checkbox.checked = task.ongoing;
  checkbox.addEventListener("change", () => run(saveSpan));
  toggle.append(checkbox, document.createTextNode("Ongoing"));
  const row = el("div", "span-row");
  row.append(
    dateField("Start", "start-date", task.ongoing ? "" : task.start, task.ongoing),
    dateField("End", "end-date", task.ongoing ? "" : task.end, task.ongoing)
  );
  wrap.append(toggle, row);
  return wrap;
}

function dateField(labelText, id, value, disabled) {
  const label = el("label", null, labelText);
  label.htmlFor = id;
  const input = el("input");
  input.id = id;
  input.type = "date";
  input.value = value || "";
  input.disabled = disabled;
  input.required = !disabled;
  input.addEventListener("change", () => run(saveSpan));
  label.append(input);
  return label;
}

function subtaskFields() {
  const wrap = el("div");
  task.subtasks.forEach((subtask) => {
    const row = el("div", "subtask-row");
    const input = el("input");
    input.type = "text";
    input.value = subtask.text;
    input.dataset.subtaskId = subtask.id;
    input.setAttribute("aria-label", "Subtask");
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") input.blur();
    });
    input.addEventListener("blur", () => {
      if (input.value === subtask.text) return;
      const id = task.id;
      const subtaskId = subtask.id;
      const value = input.value;
      run(() => saveSubtask(id, subtaskId, value));
    });
    const remove = el("button", "remove", "−");
    remove.type = "button";
    remove.setAttribute("aria-label", "Remove subtask");
    remove.addEventListener("click", () => {
      const id = task.id;
      const subtaskId = subtask.id;
      run(() => removeSubtask(id, subtaskId));
    });
    row.append(input, remove);
    wrap.append(row);
  });
  const add = el("button", "add-button", "Add subtask");
  add.type = "button";
  add.addEventListener("click", () => run(addSubtask));
  wrap.append(add);
  return wrap;
}

function linkFields() {
  const wrap = el("div");
  const form = el("div", "link-form");
  const url = el("input");
  url.id = "link-url";
  url.type = "url";
  url.placeholder = "https://app.diagrams.net/…";
  url.setAttribute("aria-label", "Link URL");
  const kind = el("select");
  kind.id = "link-kind";
  kind.setAttribute("aria-label", "Link kind");
  [["drawio", "draw.io"], ["generic", "Other"]].forEach((option) => {
    const node = el("option", null, option[1]);
    node.value = option[0];
    kind.append(node);
  });
  const label = el("input");
  label.id = "link-label";
  label.type = "text";
  label.placeholder = "Label";
  label.setAttribute("aria-label", "Link label");
  const add = el("button", "add-button", "Add link");
  add.type = "button";
  add.addEventListener("click", () => run(addLink));
  form.append(url, kind, label, add);
  wrap.append(form);

  task.links.forEach((link) => {
    const row = el("div", "record-row");
    row.append(el("span", "record-label", link.label + " · " + link.kind));
    const remove = el("button", "remove", "Remove");
    remove.type = "button";
    remove.addEventListener("click", () => {
      const id = task.id;
      const linkId = link.id;
      run(() => removeLink(id, linkId));
    });
    row.append(remove);
    wrap.append(row);
  });
  return wrap;
}

function alarmFields() {
  const wrap = el("div");
  const form = el("div", "alarm-form");
  const when = el("input");
  when.id = "alarm-time";
  when.type = "datetime-local";
  when.value = screen.selected_day + "T09:00";
  when.setAttribute("aria-label", "Alarm time");
  const add = el("button", "add-button", "Add alarm");
  add.type = "button";
  add.addEventListener("click", () => run(addAlarm));
  form.append(when, add);
  wrap.append(form);

  task.alarms.forEach((alarm) => {
    const row = el("div", "record-row");
    row.dataset.alarmId = alarm.id;
    const whenText = new Date(alarm.fires_at).toLocaleString();
    row.append(el("span", "record-label", whenText));
    row.append(el("span", "status alarm-status", alarm.status));
    const remove = el("button", "remove", "Remove");
    remove.type = "button";
    remove.addEventListener("click", () => {
      const id = task.id;
      const alarmId = alarm.id;
      run(() => removeAlarm(id, alarmId));
    });
    row.append(remove);
    wrap.append(row);
  });
  return wrap;
}

function applyFocus() {
  if (focusTarget === "title") {
    const input = document.getElementById("task-title");
    if (input) {
      input.focus();
      input.select();
    }
  } else if (focusTarget) {
    const input = document.querySelector('[data-subtask-id="' + CSS.escape(focusTarget) + '"]');
    if (input) input.focus();
  }
  focusTarget = null;
}

async function selectTask(id) {
  take(await api().snapshot(id));
}

async function createTask() {
  const result = await api().create_task();
  focusTarget = "title";
  take(result);
}

async function changeDay() {
  const value = document.getElementById("selected-day").value;
  await commit(await api().set_selected_day(value, selectedId), selectedId);
}

async function saveTitle(id, value) {
  await commit(await api().update_task(id, value, null, null, null, null), id);
}

async function saveDescription(id, value) {
  await commit(await api().update_task(id, null, value, null, null, null, null), id);
}

async function saveLocation(id, value) {
  await commit(await api().update_task(id, null, null, null, null, null, value), id);
}

async function setMode(mode) {
  take(await api().set_view_mode(mode));
}

async function saveSpan() {
  const id = selectedId;
  const ongoing = document.getElementById("ongoing").checked;
  let start = document.getElementById("start-date").value || null;
  let end = document.getElementById("end-date").value || null;
  if (ongoing) {
    start = null;
    end = null;
  } else if (!start || !end) {
    start = start || screen.selected_day;
    end = end || screen.selected_day;
  }
  await commit(await api().update_task(id, null, null, start, end, ongoing), id);
}

async function commit(result, id) {
  if (result.ok) {
    take(result);
    return;
  }
  const fresh = await api().snapshot(id);
  take(fresh);
  showError(result.error);
}

async function deleteTask() {
  const id = selectedId;
  retainedId = id;
  take(await api().delete_task(id));
}

async function addSubtask() {
  const result = await api().add_subtask(selectedId, "");
  if (result.ok && result.task && result.task.subtasks.length) {
    focusTarget = result.task.subtasks[result.task.subtasks.length - 1].id;
  }
  take(result);
}

async function saveSubtask(id, subtaskId, value) {
  await commit(await api().update_subtask(id, subtaskId, value), id);
}

async function removeSubtask(id, subtaskId) {
  take(await api().delete_subtask(id, subtaskId));
}

async function addLink() {
  const result = await api().add_link(
    selectedId,
    document.getElementById("link-url").value.trim(),
    document.getElementById("link-kind").value,
    document.getElementById("link-label").value
  );
  await commit(result, selectedId);
}

async function removeLink(id, linkId) {
  take(await api().delete_link(id, linkId));
}

async function addAlarm() {
  const result = await api().add_alarm(selectedId, document.getElementById("alarm-time").value);
  await commit(result, selectedId);
}

async function removeAlarm(id, alarmId) {
  take(await api().delete_alarm(id, alarmId));
}

async function undo() {
  const id = selectedId || retainedId;
  const result = await api().undo(id);
  if (result.ok && result.task) retainedId = null;
  take(result);
}

async function redo() {
  const id = selectedId || retainedId;
  const result = await api().redo(id);
  if (result.ok && result.task) retainedId = null;
  take(result);
}

async function openLink(url) {
  const result = await api().open_link(url);
  if (!result.ok) showError(result.error);
}

function renderBanner() {
  const banner = document.getElementById("banner");
  const list = document.getElementById("banner-list");
  list.replaceChildren();
  if (!notices.length) {
    banner.hidden = true;
    return;
  }
  banner.hidden = false;
  notices.forEach((event) => {
    const when = new Date(event.fires_at).toLocaleString();
    const prefix = event.status === "missed" ? "Missed" : "Alarm";
    list.append(el("li", null, prefix + ": " + event.task_title + " at " + when));
  });
}

function addNotices(events) {
  events.forEach((event) => {
    notices.push(event);
    if (task && task.id === event.task_id) {
      const alarm = task.alarms.find((item) => item.id === event.alarm_id);
      if (alarm) alarm.status = event.status;
      const status = document.querySelector(
        '[data-alarm-id="' + CSS.escape(event.alarm_id) + '"] .alarm-status'
      );
      if (status) status.textContent = event.status;
    }
  });
  renderBanner();
}

function startPolling() {
  window.setInterval(async () => {
    if (polling) return;
    polling = true;
    try {
      const result = await api().drain_alarm_events();
      if (result.ok && result.events.length) addNotices(result.events);
    } finally {
      polling = false;
    }
  }, 1000);
}

function onKey(event) {
  if (event.target.closest("input, textarea, select")) return;
  if (!event.ctrlKey) return;
  const key = event.key.toLowerCase();
  if (key === "z" && !event.shiftKey) {
    event.preventDefault();
    if (screen && screen.can_undo) run(undo);
  } else if (key === "y") {
    event.preventDefault();
    if (screen && screen.can_redo) run(redo);
  }
}

document.getElementById("new-task").addEventListener("click", () => run(createTask));
document.getElementById("undo").addEventListener("click", () => run(undo));
document.getElementById("redo").addEventListener("click", () => run(redo));
document.getElementById("mode-summary").addEventListener("click", () => run(() => setMode("summary")));
document.getElementById("mode-detailed").addEventListener("click", () => run(() => setMode("detailed")));
document.getElementById("logout").addEventListener("click", logout);
document.getElementById("chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = document.getElementById("chat-input");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  run(() => sendChat(message));
});
document.getElementById("selected-day").addEventListener("change", () => run(changeDay));
document.getElementById("banner-dismiss").addEventListener("click", () => {
  notices = [];
  renderBanner();
});
document.addEventListener("keydown", onKey);

function showGate(body) {
  document.getElementById("app").hidden = true;
  document.getElementById("gate").hidden = false;
  const missing = document.getElementById("gate-missing");
  const signIn = document.getElementById("sign-in");
  const names = body && body.missing ? body.missing : [];
  if (names.length) {
    missing.hidden = false;
    missing.textContent = "Add these to .env before signing in: " + names.join(", ") + ". Accounts are managed at manage.auth0.com.";
    signIn.hidden = true;
  } else {
    missing.hidden = true;
    signIn.hidden = false;
  }
  const params = new URLSearchParams(window.location.search);
  if (params.get("auth") === "error") {
    document.getElementById("gate-copy").textContent = "Auth0 did not complete the sign-in. Try again.";
  }
}

function renderChat() {
  const log = document.getElementById("chat-log");
  log.replaceChildren();
  if (!chatMessages.length) {
    log.append(el("p", "chat-empty", "Ask the assistant to read today's tasks, or to create one."));
    return;
  }
  chatMessages.forEach((entry) => {
    log.append(el("p", "chat-" + entry.role, entry.text));
  });
  log.scrollTop = log.scrollHeight;
}

async function sendChat(message) {
  chatMessages.push({ role: "user", text: message });
  renderChat();
  const response = await fetch("/api/chat", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (response.status === 401) {
    showGate(await response.json());
    return;
  }
  const result = await response.json();
  if (!result.ok) {
    chatMessages.push({ role: "assistant", text: result.error || "The assistant could not answer." });
    renderChat();
    showError(result.error);
    return;
  }
  chatMessages.push({ role: "assistant", text: result.reply || "Done." });
  renderChat();
  take(result);
}

async function logout() {
  await fetch("/logout", { method: "POST", credentials: "same-origin" });
  window.location.href = "/";
}

let started = false;

async function boot() {
  if (started) return;
  started = true;
  const response = await fetch("/api/session", { credentials: "same-origin" });
  if (response.status === 401) {
    showGate(await response.json());
    return;
  }
  document.getElementById("gate").hidden = true;
  document.getElementById("app").hidden = false;
  renderChat();
  run(async () => {
    take(await api().snapshot(null));
    startPolling();
  });
}

window.addEventListener("DOMContentLoaded", boot);
