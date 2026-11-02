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
let openFields = new Set();
let openFor = null;
let showCalendar = false;
let calendarCursor = null;

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
  const nextId = result.task ? result.task.id : null;
  if (nextId !== openFor) {
    openFields.clear();
    openFor = nextId;
  }
  screen = result.screen;
  task = result.task;
  selectedId = nextId;
  showError("");
  render();
  return true;
}

function render() {
  document.getElementById("undo").disabled = !screen.can_undo;
  document.getElementById("redo").disabled = !screen.can_redo;
  const dayButton = document.getElementById("selected-day");
  dayButton.textContent = formatDay(screen.selected_day);
  dayButton.classList.toggle("highlighted", showCalendar);
  dayButton.setAttribute("aria-pressed", showCalendar ? "true" : "false");
  if (showCalendar && !calendarCursor) calendarCursor = screen.selected_day.slice(0, 7);
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
    const when = timeOnDay(item, screen.selected_day);
    if (when) open.append(el("div", "card-span", when));
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
    open.addEventListener("click", () => {
      showCalendar = false;
      run(() => selectTask(item.id));
    });
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

function timeOnDay(item, day) {
  const times = (item.alarms || []).map((alarm) => alarm.fires_at).filter((value) => value.slice(0, 10) === day);
  if (!times.length) return "";
  times.sort();
  return new Date(times[0]).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

function renderDetail() {
  const detail = document.getElementById("detail");
  detail.classList.toggle("showing-calendar", showCalendar);
  detail.replaceChildren();
  if (showCalendar) {
    detail.append(monthCalendar());
    return;
  }
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

  detail.append(optionalSection("description", "Description", Boolean((task.description || "").trim()), descriptionField));
  detail.append(optionalSection("location", "Location", Boolean((task.location || "").trim()), locationField));
  detail.append(section("Dates", spanFields()));
  detail.append(optionalSection("subtasks", "Subtasks", task.subtasks.length > 0, subtaskFields));
  detail.append(optionalSection("links", "Links", task.links.length > 0, linkFields));
  detail.append(optionalSection("alarms", "Alarms", task.alarms.length > 0, alarmFields));
}

function monthCalendar() {
  const [year, month] = calendarCursor.split("-").map(Number);
  const wrap = el("div", "month-calendar");
  const header = el("div", "month-header");
  const previous = el("button", "day-step", "<");
  previous.type = "button";
  previous.setAttribute("aria-label", "Previous month");
  previous.addEventListener("click", () => {
    calendarCursor = shiftMonth(calendarCursor, -1);
    render();
  });
  const next = el("button", "day-step", ">");
  next.type = "button";
  next.setAttribute("aria-label", "Next month");
  next.addEventListener("click", () => {
    calendarCursor = shiftMonth(calendarCursor, 1);
    render();
  });
  const title = new Date(year, month - 1, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" });
  header.append(previous, el("h2", "month-title", title), next);

  const weekdays = el("div", "month-weekdays");
  for (let index = 0; index < 7; index += 1) {
    const name = new Date(2026, 5, 7 + index).toLocaleDateString(undefined, { weekday: "short" });
    weekdays.append(el("div", "month-weekday", name));
  }

  const grid = el("div", "month-grid");
  const firstWeekday = new Date(year, month - 1, 1).getDay();
  const daysInMonth = new Date(year, month, 0).getDate();
  const total = Math.ceil((firstWeekday + daysInMonth) / 7) * 7;
  for (let index = 0; index < total; index += 1) {
    const date = new Date(year, month - 1, index - firstWeekday + 1);
    const iso = isoFromDate(date);
    const classes = ["month-day"];
    if (date.getMonth() !== month - 1) classes.push("outside");
    if (iso === screen.today) classes.push("today");
    if (iso === screen.selected_day) classes.push("selected");
    const button = el("button", classes.join(" "));
    button.type = "button";
    button.append(el("span", "day-number", String(date.getDate())));
    const busy = dayHasTasks(iso);
    if (busy) button.append(el("span", "day-dot"));
    button.setAttribute("aria-label", formatDay(iso) + (busy ? ", has tasks" : ""));
    button.addEventListener("click", () => run(() => selectCalendarDay(iso)));
    grid.append(button);
  }
  wrap.append(header, weekdays, grid);
  return wrap;
}

function dayHasTasks(iso) {
  return (screen.spans || []).some((span) => {
    if (span.ongoing) return true;
    if (!span.start) return false;
    const end = span.end || span.start;
    return iso >= span.start && iso <= end;
  });
}

function isoFromDate(date) {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return date.getFullYear() + "-" + month + "-" + day;
}

function shiftMonth(cursor, delta) {
  const [year, month] = cursor.split("-").map(Number);
  return isoFromDate(new Date(year, month - 1 + delta, 1)).slice(0, 7);
}

async function selectCalendarDay(iso) {
  calendarCursor = iso.slice(0, 7);
  await commit(await api().set_selected_day(iso, selectedId), selectedId);
}

function optionalSection(name, heading, filled, body) {
  if (!filled && !openFields.has(name)) {
    return section(heading, addIcon("Add " + heading.toLowerCase(), () => revealField(name)));
  }
  return section(heading, body());
}

function addIcon(label, action) {
  const button = el("button", "add-icon", "+");
  button.type = "button";
  button.setAttribute("aria-label", label);
  button.addEventListener("click", () => run(action));
  return button;
}

function revealField(name) {
  if (name === "subtasks") {
    return addSubtask();
  }
  openFields.add(name);
  focusTarget = { description: "task-description", location: "task-location", links: "link-url", alarms: "alarm-time" }[name] || null;
  renderDetail();
  applyFocus();
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
    const value = field.value;
    if (!value.trim()) openFields.delete("location");
    if (value === (task.location || "")) {
      if (!value.trim()) renderDetail();
      return;
    }
    const id = task.id;
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
  field.placeholder = "Add a description";
  field.setAttribute("aria-label", "Description");
  field.addEventListener("blur", () => {
    const value = field.value;
    if (!value.trim()) openFields.delete("description");
    if (value === task.description) {
      if (!value.trim()) renderDetail();
      return;
    }
    const id = task.id;
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
  wrap.append(addIcon("Add subtask", addSubtask));
  return wrap;
}

function linkFields() {
  const wrap = el("div");
  if (openFields.has("links")) {
    wrap.append(linkForm());
  }
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
  if (!openFields.has("links")) wrap.append(addIcon("Add link", () => revealField("links")));
  return wrap;
}

function linkForm() {
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
  return form;
}

function alarmFields() {
  const wrap = el("div");
  if (openFields.has("alarms")) wrap.append(alarmForm());
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
  if (!openFields.has("alarms")) wrap.append(addIcon("Add alarm", () => revealField("alarms")));
  return wrap;
}

function alarmForm() {
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
  return form;
}

function applyFocus() {
  if (focusTarget === "title") {
    const input = document.getElementById("task-title");
    if (input) {
      input.focus();
      input.select();
    }
  } else if (focusTarget === "task-description" || focusTarget === "task-location" || focusTarget === "link-url" || focusTarget === "alarm-time") {
    const input = document.getElementById(focusTarget);
    if (input) input.focus();
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
  showCalendar = false;
  const result = await api().create_task();
  focusTarget = "title";
  take(result);
}

async function shiftDay(days) {
  if (!screen || !screen.selected_day) return;
  const [year, month, day] = screen.selected_day.split("-").map(Number);
  const shifted = new Date(Date.UTC(year, month - 1, day + days));
  const iso = shifted.toISOString().slice(0, 10);
  calendarCursor = iso.slice(0, 7);
  await commit(await api().set_selected_day(iso, selectedId), selectedId);
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
  if (result.ok) openFields.delete("links");
  await commit(result, selectedId);
}

async function removeLink(id, linkId) {
  take(await api().delete_link(id, linkId));
}

async function addAlarm() {
  const result = await api().add_alarm(selectedId, document.getElementById("alarm-time").value);
  if (result.ok) openFields.delete("alarms");
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
document.getElementById("logout").addEventListener("click", logout);
document.getElementById("chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = document.getElementById("chat-input");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  run(() => sendChat(message));
});
document.getElementById("previous-day").addEventListener("click", () => run(() => shiftDay(-1)));
document.getElementById("next-day").addEventListener("click", () => run(() => shiftDay(1)));
document.getElementById("selected-day").addEventListener("click", () => {
  if (!screen) return;
  showCalendar = !showCalendar;
  if (showCalendar) calendarCursor = screen.selected_day.slice(0, 7);
  render();
});
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
