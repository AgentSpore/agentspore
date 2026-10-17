const META = Object.freeze({ format: "agentspore-emergency-binder", version: 1,
  kdf: "PBKDF2-SHA256", iterations: 600000, cipher: "AES-256-GCM" });
const AAD = new TextEncoder().encode("agentspore.emergency-binder:v1");
const element = (id) => document.getElementById(id);
const encode = (bytes) => btoa(Array.from(bytes, (byte) => String.fromCharCode(byte)).join(""));

function passwordValid(value) {
  const points = [...value];
  return points.length >= 12 && points.length <= 128 && points.every((point) => {
    const code = point.codePointAt(0);
    return code < 0xD800 || code > 0xDFFF;
  });
}

function decode(value) {
  if (typeof value !== "string" || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) {
    throw new Error("invalid_backup");
  }
  const bytes = Uint8Array.from(atob(value), (char) => char.charCodeAt(0));
  if (encode(bytes) !== value) throw new Error("invalid_backup");
  return bytes;
}

function validateEnvelope(value) {
  const keys = [...Object.keys(META), "salt", "iv", "ciphertext"];
  if (!value || typeof value !== "object" || Array.isArray(value)
    || Object.keys(value).length !== keys.length || !keys.every((key) => Object.hasOwn(value, key))
    || Object.entries(META).some(([key, expected]) => value[key] !== expected)) throw new Error("invalid_backup");
  const salt = decode(value.salt), iv = decode(value.iv), ciphertext = decode(value.ciphertext);
  if (salt.length !== 16 || iv.length !== 12 || ciphertext.length < 16 || ciphertext.length > 65552) {
    throw new Error("invalid_backup");
  }
  return { salt, iv, ciphertext };
}

async function deriveKey(password, salt) {
  const bytes = new TextEncoder().encode(password);
  try {
    const material = await crypto.subtle.importKey("raw", bytes, "PBKDF2", false, ["deriveKey"]);
    return await crypto.subtle.deriveKey({ name: "PBKDF2", hash: "SHA-256", iterations: 600000, salt },
      material, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  } finally {
    bytes.fill(0);
  }
}

async function encryptSnapshot(snapshot, password) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const plaintext = new TextEncoder().encode(JSON.stringify(snapshot));
  try {
    if (plaintext.length > 65536) throw new Error("invalid_backup");
    const key = await deriveKey(password, salt);
    const bytes = await crypto.subtle.encrypt({ name: "AES-GCM", iv, tagLength: 128, additionalData: AAD }, key, plaintext);
    return { ...META, salt: encode(salt), iv: encode(iv), ciphertext: encode(new Uint8Array(bytes)) };
  } finally {
    plaintext.fill(0);
  }
}

async function decryptFile(file, password, validateDocument) {
  if (!file || file.size > 98304) throw new Error("invalid_backup");
  const raw = new Uint8Array(await file.arrayBuffer());
  let plaintext;
  try {
    if (raw.length > 98304) throw new Error("invalid_backup");
    const envelope = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw));
    const { salt, iv, ciphertext } = validateEnvelope(envelope);
    const key = await deriveKey(password, salt);
    plaintext = new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv, tagLength: 128,
      additionalData: AAD }, key, ciphertext));
    if (plaintext.length > 65536) throw new Error("invalid_backup");
    const document = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(plaintext));
    if (!validateDocument(document)) throw new Error("invalid_backup");
    return document;
  } finally {
    raw.fill(0);
    plaintext?.fill(0);
  }
}

class BackupController {
  constructor(context) {
    this.context = context;
    this.busy = false;
    this.candidate = null;
    this.clearGeneration = context.getClearGeneration();
    this.supported = window.isSecureContext && Boolean(window.crypto?.subtle);
    this.message = this.supported ? "" : "backupUnavailable";
    this.status = "idle";
  }

  show(key, state = "idle") {
    this.message = key;
    this.status = state;
    if (!this.context.isFormValid()) return;
    const node = element("status");
    node.dataset.owner = "backup";
    node.dataset.state = state;
    node.textContent = key ? this.context.text(key) : "";
  }

  refresh() {
    if (this.clearGeneration !== this.context.getClearGeneration()) {
      this.clearGeneration = this.context.getClearGeneration();
      this.candidate = null;
      this.clearSecrets();
      if (!this.busy) this.show("", "idle");
    }
    if (this.candidate && this.candidate.revision !== this.context.getRevision()) {
      this.candidate = null;
      this.show("backupError", "error");
    }
    element("backup-controls").setAttribute("aria-busy", String(this.busy));
    for (const id of ["backup-password", "backup-confirm", "backup-file"]) element(id).disabled = !this.supported || this.busy;
    const password = element("backup-password").value;
    element("backup-export").disabled = !this.supported || this.busy || !this.context.isFormValid()
      || !passwordValid(password) || password !== element("backup-confirm").value;
    element("backup-import").disabled = !this.supported || this.busy || !passwordValid(password) || !element("backup-file").files.length;
    for (const id of ["restore-confirm", "restore-cancel"]) {
      element(id).hidden = !this.candidate;
      element(id).disabled = this.busy || !this.candidate;
    }
    if (this.busy || element("status").dataset.owner === "backup") this.show(this.message, this.status);
  }

  clearSecrets() {
    for (const id of ["backup-password", "backup-confirm", "backup-file"]) element(id).value = "";
  }

  download(envelope) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(envelope)], { type: "application/json" }));
    try {
      const link = document.createElement("a");
      link.href = url;
      link.download = "family-folder.enc.json";
      document.body.append(link);
      link.click();
      link.remove();
      this.context.event("backup_download");
    } finally {
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
  }

  async run(operation) {
    if (this.busy || !this.supported) return;
    let password = element("backup-password").value;
    if (!passwordValid(password)) return;
    if (operation === "export" && password !== element("backup-confirm").value) return;
    const generation = this.context.getClearGeneration();
    const revision = this.context.getRevision();
    const file = element("backup-file").files[0];
    this.candidate = null;
    this.busy = true;
    this.show("backupBusy", "busy");
    this.refresh();
    try {
      if (operation === "export") {
        const snapshot = this.context.getSnapshot();
        const envelope = await encryptSnapshot(snapshot, password);
        if (generation !== this.context.getClearGeneration()) return;
        this.download(envelope);
        this.show("backupSaved", "success");
      } else {
        const document = await decryptFile(file, password, this.context.validateDocument);
        if (generation !== this.context.getClearGeneration()) return;
        if (revision !== this.context.getRevision()) throw new Error("stale_document");
        this.candidate = { document, revision };
        this.show("backupCandidate", "candidate");
      }
    } catch {
      if (generation === this.context.getClearGeneration()) this.show("backupError", "error");
    } finally {
      password = "";
      this.clearSecrets();
      this.busy = false;
      if (generation !== this.context.getClearGeneration()) this.show("", "idle");
      this.refresh();
    }
  }

  confirm() {
    const candidate = this.candidate;
    this.candidate = null;
    const applied = candidate && this.context.replaceDocument(candidate.document, candidate.revision);
    this.show(applied ? "backupRestored" : "backupError", applied ? "success" : "error");
    this.refresh();
  }

  credentialsChanged() {
    this.candidate = null;
    if (!this.busy) this.show("");
    this.refresh();
  }

  bind() {
    element("backup-controls").hidden = false;
    for (const id of ["backup-password", "backup-confirm", "backup-file"]) element(id).addEventListener("input", () => this.credentialsChanged());
    element("backup-file").addEventListener("change", () => this.credentialsChanged());
    element("backup-export").addEventListener("click", () => this.run("export"));
    element("backup-import").addEventListener("click", () => this.run("import"));
    element("restore-confirm").addEventListener("click", () => this.confirm());
    element("restore-cancel").addEventListener("click", () => {
      this.candidate = null;
      this.show("backupCancelled");
      this.refresh();
    });
    this.show(this.message);
    this.refresh();
  }
}

/** Bind two local backup paths to the existing document without circular imports. */
export function initializeBackup(context) {
  const controller = new BackupController(context);
  controller.bind();
  return { refresh: () => controller.refresh() };
}
