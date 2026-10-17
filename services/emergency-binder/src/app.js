import { initializeBackup } from "./backup.js";

const SECTIONS = ["contacts", "home", "documents", "accounts", "care"];
const CARE = ["contacts", "home", "care"];
const element = (id) => document.getElementById(id);
const emptySections = () => Object.fromEntries(SECTIONS.map((id) => [id, { primary: "", notes: "" }]));
let state = { version: 1, country: "US", lang: "en", mode: "family", sections: emptySections() };
let active = "contacts";
let revision = 0;
let clearGeneration = 0;
let catalogs;
let countries;
let backupUI;
let draftValid = true;
const reviewed = new Set();
const copy = () => catalogs[state.lang];
const visibleSections = () => state.mode === "care" ? CARE : SECTIONS;

function validText(value) {
  if (typeof value !== "string") return false;
  const points = [...value];
  return points.length <= 1000 && points.every((point) => {
    const code = point.codePointAt(0);
    return code < 0xD800 || code > 0xDFFF;
  });
}

function sameKeys(value, keys) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

/** Validate the bounded document shared by printing and future backup/restore. */
export function validateDocument(value) {
  if (!sameKeys(value, ["version", "country", "lang", "mode", "sections"])) return false;
  if (value.version !== 1 || !["US", "GB"].includes(value.country)
    || !["en", "ru"].includes(value.lang) || !["family", "care"].includes(value.mode)) return false;
  return sameKeys(value.sections, SECTIONS) && SECTIONS.every((id) => {
    const section = value.sections[id];
    return sameKeys(section, ["primary", "notes"]) && validText(section.primary) && validText(section.notes);
  }) && new TextEncoder().encode(JSON.stringify(value)).length <= 65536;
}

/** Return a detached, validated snapshot; never expose the live RAM document. */
export function getDocumentSnapshot() {
  if (!catalogs || !commitFields() || !validateDocument(state)) throw new Error("invalid_document");
  return structuredClone(state);
}

/** Edits and setting changes invalidate a pending restore candidate. */
export const getRevision = () => revision;
/** Clear also invalidates delivery of a pending encrypted export. */
export const getClearGeneration = () => clearGeneration;

/** Replace only a valid candidate captured against the unchanged revision. */
export function replaceDocument(value, expectedRevision) {
  if (!catalogs || !commitFields() || revision !== expectedRevision || !validateDocument(value)) return false;
  state = structuredClone(value);
  revision += 1;
  reviewed.clear();
  active = "contacts";
  renderAll();
  return true;
}

function recordEvent(event, section = "overview") {
  // Metrics are best-effort; an unavailable gateway must not block private local work.
  void fetch("/_agentspore/events", {
    method: "POST", credentials: "omit", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ event, section, lang: state.lang }), signal: AbortSignal.timeout(3000),
  }).catch(() => undefined);
}

function textElement(tag, text) {
  const node = document.createElement(tag);
  node.textContent = text;
  return node;
}

function renderPreview() {
  const heading = textElement("h2", copy().ui[state.mode]);
  heading.id = "preview-title";
  const nodes = [heading, textElement("p", countries[state.country][state.lang].name)];
  for (const id of visibleSections()) {
    const values = state.sections[id];
    if (!values.primary && !values.notes) continue;
    const section = document.createElement("section");
    section.append(textElement("h3", copy().sections[id].title));
    for (const value of [values.primary, values.notes]) {
      if (value) section.append(textElement("p", value));
    }
    nodes.push(section);
  }
  element("print-preview").replaceChildren(...nodes);
}

function commitFields() {
  const values = { primary: element("section-primary").value, notes: element("section-notes").value };
  const valid = validText(values.primary) && validText(values.notes);
  if (values.primary !== state.sections[active].primary || values.notes !== state.sections[active].notes) {
    revision += 1;
    reviewed.delete(active);
    if (valid) state.sections[active] = values;
  }
  for (const name of ["primary", "notes"]) {
    element(`section-${name}`).setAttribute("aria-invalid", String(!validText(values[name])));
  }
  draftValid = valid;
  if (!valid || element("status").dataset.owner !== "backup") {
    element("status").textContent = valid ? "" : copy().ui.limitError;
    element("status").dataset.owner = "form";
    element("status").dataset.state = valid ? "idle" : "error";
  }
  element("print-folder").disabled = !valid;
  element("mark-complete").disabled = !valid || reviewed.has(active);
  renderPreview();
  backupUI?.refresh();
  return valid;
}

function renderEditor() {
  const labels = copy().sections[active];
  element("section-title").textContent = labels.title;
  element("primary-label").textContent = labels.primary;
  element("notes-label").textContent = labels.notes;
  element("section-primary").value = state.sections[active].primary;
  element("section-notes").value = state.sections[active].notes;
  const buttons = visibleSections().map((id) => {
    const button = textElement("button", copy().sections[id].title);
    button.type = "button";
    button.dataset.section = id;
    button.setAttribute("aria-current", String(id === active));
    button.addEventListener("click", () => openSection(id));
    return button;
  });
  element("section-nav").replaceChildren(...buttons);
  commitFields();
}

function openSection(id) {
  if (!commitFields()) return;
  active = id;
  renderEditor();
  recordEvent("section_open", id);
  element("section-title").focus();
}

function renderAll() {
  document.documentElement.lang = state.lang;
  document.title = copy().ui.title;
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    node.textContent = copy().ui[node.dataset.i18n];
  });
  document.querySelectorAll("[data-i18n-aria]").forEach((node) => {
    node.setAttribute("aria-label", copy().ui[node.dataset.i18nAria]);
  });
  for (const key of ["language", "country", "mode"]) {
    element(key).value = state[key === "language" ? "lang" : key];
  }
  for (const option of element("country").options) {
    option.textContent = countries[option.value][state.lang].name;
  }
  element("country-note").textContent = countries[state.country][state.lang].prompt;
  element("mode-summary").textContent = copy().ui[`${state.mode}Summary`];
  if (!visibleSections().includes(active)) active = "contacts";
  renderEditor();
}

function changeSetting(event) {
  const key = event.target.id === "language" ? "lang" : event.target.id;
  if (!commitFields()) {
    event.target.value = state[key];
    return;
  }
  const next = { ...state, [key]: event.target.value };
  if (!validateDocument(next)) {
    event.target.value = state[key];
    return;
  }
  state = next;
  revision += 1;
  renderAll();
}

function clearFolder() {
  if (!window.confirm(copy().ui.clearConfirm)) return;
  state.sections = emptySections();
  revision += 1;
  clearGeneration += 1;
  reviewed.clear();
  renderAll();
}

async function initialize() {
  try {
    const files = await Promise.all(["en", "ru", "countries"].map(async (name) => {
      const response = await fetch(`content/${name}.json`, { credentials: "omit" });
      if (!response.ok) throw new Error("catalog_unavailable");
      return response.json();
    }));
    catalogs = { en: files[0], ru: files[1] };
    countries = files[2];
    renderAll();
    for (const id of ["language", "country", "mode"]) element(id).addEventListener("change", changeSetting);
    for (const id of ["section-primary", "section-notes"]) element(id).addEventListener("input", commitFields);
    element("mark-complete").addEventListener("click", () => {
      if (!commitFields() || reviewed.has(active)) return;
      reviewed.add(active);
      element("mark-complete").disabled = true;
      recordEvent("section_complete", active);
    });
    element("print-folder").addEventListener("click", () => {
      if (!commitFields()) return;
      recordEvent("print_request");
      window.print();
    });
    element("clear-folder").addEventListener("click", clearFolder);
    const request = new URL("https://github.com/AgentSpore/agentspore/issues/new");
    request.searchParams.set("title", "Country or region request");
    request.searchParams.set("body", "Country or region requested: [fill in GitHub]");
    element("request-country").href = request.href;
    backupUI = initializeBackup({ getSnapshot: getDocumentSnapshot, getRevision, getClearGeneration,
      replaceDocument, validateDocument, text: (key) => copy().ui[key], event: recordEvent,
      isFormValid: () => draftValid });
    recordEvent("folder_open");
    recordEvent("section_open", active);
    for (const id of ["language", "country", "mode", "section-primary", "section-notes", "clear-folder"]) {
      element(id).disabled = false;
    }
  } catch {
    element("status").textContent = catalogs ? copy().ui.loadError : "Unable to load / Не удалось загрузить";
  }
}

void initialize();
