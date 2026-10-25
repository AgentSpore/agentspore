/* Observe application reads without cloning, draining, or retaining body content.
 * Fetch/read wrappers change promise identity and microtask timing. */
{
  const state = {documentID: crypto.randomUUID(), records: [], overflow: 0};
  const bodies = new WeakMap(), readers = new WeakMap(), hashes = [];
  const flag = (headers, name) => {
    let value = null;
    if (headers instanceof Headers) value = headers.get(name);
    else if (Array.isArray(headers)) {
      const values = headers.filter(([key]) => key.toLowerCase() === name);
      if (values.length > 1) return null;
      value = values[0]?.[1] ?? null;
    } else if (headers) {
      const keys = Object.keys(headers).filter(key => key.toLowerCase() === name);
      if (keys.length > 1) return null;
      value = keys.length ? headers[keys[0]] : null;
    }
    return value === '1' ? true : value === null ? false : null;
  };
  const create = fields => {
    if (state.records.length >= 200) {state.overflow++; return null;}
    const record = {id: state.records.length + 1, bytes: 0, done: 0,
      errors: 0, reads: 0, pending: 0, readerCancel: 0, bodyCancel: 0, releases: 0, status: null, mime: null, ...fields};
    state.records.push(record);
    return record;
  };
  const targetPayload = url => {
    const query = url.searchParams, values = query.getAll('_rsc'), next = query.getAll('next');
    const validNext = next.length === 0 || url.pathname === '/login' && next.length === 1 && next[0] === '/workspace';
    if (!CONNECT_READ_PAGES.includes(url.pathname) || Array.from(query.keys()).some(key => !['_rsc', 'next'].includes(key)) ||
        values.length !== 1 || !/^[A-Za-z0-9_-]+$/.test(values[0]) || !validNext) return null;
    return JSON.stringify([url.pathname, next.length ? 'workspace-return' : '', values[0]]);
  };
  const originalFetch = globalThis.fetch;
  globalThis.fetch = function (...args) {
    let record = null;
    try {
      const input = args[0], options = args[1];
      const url = new URL(input instanceof Request ? input.url : input, location.href);
      const headers = options?.headers ?? (input instanceof Request ? input.headers : null);
      record = create({control: false, path: url.pathname, key: null,
        local: url.origin === location.origin,
        method: options?.method ?? (input instanceof Request ? input.method : 'GET'),
        rsc: flag(headers, 'rsc'), prefetch: flag(headers, 'next-router-prefetch')});
      const payload = targetPayload(url);
      if (record && payload !== null) {
        hashes.push(crypto.subtle.digest('SHA-256', new TextEncoder().encode(
          payload)).then(bytes => {
          record.key = Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2, '0')).join('');
        }));
      }
    } catch {state.overflow++;}
    const promise = Reflect.apply(originalFetch, this, args);
    if (!record) return promise;
    return promise.then(response => {
      record.status = response.status;
      record.mime = response.headers.get('content-type')?.split(';')[0] === 'text/x-component'
        ? 'text/x-component' : '<other>';
      if (response.body) bodies.set(response.body, record);
      return response;
    });
  };
  const originalGet = ReadableStream.prototype.getReader;
  ReadableStream.prototype.getReader = function (...args) {
    const reader = Reflect.apply(originalGet, this, args), record = bodies.get(this);
    if (record) readers.set(reader, record);
    return reader;
  };
  for (const [prototype, method, field, owners] of [
    [ReadableStreamDefaultReader.prototype, 'cancel', 'readerCancel', readers],
    [ReadableStream.prototype, 'cancel', 'bodyCancel', bodies],
    [ReadableStreamDefaultReader.prototype, 'releaseLock', 'releases', readers],
  ]) {
    const original = prototype[method];
    prototype[method] = function (...args) {
      const record = owners.get(this);
      if (record) record[field]++;
      return Reflect.apply(original, this, args);
    };
  }
  const originalRead = ReadableStreamDefaultReader.prototype.read;
  ReadableStreamDefaultReader.prototype.read = function (...args) {
    const record = readers.get(this);
    if (record) {record.reads++; record.pending++;}
    const promise = Reflect.apply(originalRead, this, args);
    if (!record) return promise;
    return promise.then(value => {
      record.pending--;
      if (value.done) record.done++;
      else record.bytes += value.value?.byteLength || 0;
      return value;
    }, error => {
      if (record) {record.pending--; record.errors++;}
      throw error;
    });
  };
  const cancelControl = async kind => {
    const record = create({control: true, kind});
    let controller;
    const stream = new ReadableStream({start(value) {controller = value; value.enqueue(new Uint8Array([1, 2, 3]));}});
    bodies.set(stream, record);
    let reader = stream.getReader();
    await reader.read();
    if (kind === 'reader-cancel') await reader.cancel();
    else {
      reader.releaseLock();
      if (kind === 'body-cancel') await stream.cancel(); else controller.close();
      reader = stream.getReader();
    }
    await reader.read();
    return record.bytes === 3 && record.done === 1 && record.errors === 0 &&
      record.pending === 0 && record.readerCancel === (kind === 'reader-cancel' ? 1 : 0) &&
      record.bodyCancel === (kind === 'body-cancel' ? 1 : 0) && record.releases === (kind === 'reader-cancel' ? 0 : 1);
  };
  const controls = (async () => {
    const positive = create({control: true, kind: 'EOF'});
    const response = new Response(new Uint8Array([1, 2, 3]));
    bodies.set(response.body, positive);
    const reader = response.body.getReader();
    await reader.read(); await reader.read();
    const negative = create({control: true, kind: 'read-error'});
    const stream = new ReadableStream({pull(controller) {controller.error(new Error('Synthetic read rejection'));}});
    bodies.set(stream, negative);
    try {await stream.getReader().read();} catch { /* Expected control rejection. */ }
    const cancellations = await Promise.all(['reader-cancel', 'body-cancel', 'release'].map(cancelControl));
    return cancellations.every(Boolean) && positive.readerCancel === 0 && positive.bodyCancel === 0 && positive.releases === 0 && positive.bytes === 3 && positive.done === 1 && positive.errors === 0 &&
      negative.errors === 1 && negative.done === 0 && negative.pending === 0;
  })();
  Object.defineProperty(globalThis, '__connectReadSnapshot', {value: async () => {
    const verified = await controls;
    await Promise.all(hashes);
    return {...state, verified};
  }});
}
