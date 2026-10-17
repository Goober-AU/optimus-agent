// Local, in-process inspector evaluation; no debugging port is opened.
// REPL mode preserves lexical bindings and permits top-level await.
(() => {
  const { Session } = require('node:inspector');
  const { createInterface } = require('node:readline');
  const { createRequire } = require('node:module');
  const { resolve, isAbsolute } = require('node:path');
  const { pathToFileURL } = require('node:url');
  const { inspect } = require('node:util');
  const { AsyncLocalStorage } = require('node:async_hooks');
  const session = new Session();
  session.connect();
  const post = (method, params) => new Promise((resolve, reject) =>
    session.post(method, params, (error, result) => error ? reject(error) : resolve(result)));
  const write = process.stdout.write.bind(process.stdout);
  const emit = (value) => write(JSON.stringify(value) + '\n');
  const cells = new AsyncLocalStorage();
  const limit = 64 * 1024;
  const streamNotice = '\n[Node output truncated at 64 KiB]\n';
  const resultNotice = '\n[Node result truncated at 64 KiB]\n';
  // The budget counts UTF-8 bytes, so every clip must operate on the encoded
  // buffer and back off to a sequence boundary; slicing the string itself
  // counts UTF-16 code units and can split a surrogate pair.
  const asUtf8 = (chunk) => {
    if (typeof chunk === 'string') return Buffer.from(chunk, 'utf8');
    if (Buffer.isBuffer(chunk)) return chunk;
    if (chunk && chunk.buffer instanceof ArrayBuffer && typeof chunk.byteLength === 'number') {
      return Buffer.from(chunk.buffer, chunk.byteOffset ?? 0, chunk.byteLength);
    }
    return Buffer.from(String(chunk), 'utf8');
  };
  const clipBytes = (buffer, budget) => {
    let end = Math.min(buffer.length, budget);
    while (end > 0 && (buffer[end] & 0xc0) === 0x80) end -= 1;
    return end;
  };
  const clipText = (text) => {
    const buffer = asUtf8(text);
    const end = clipBytes(buffer, limit);
    const clipped = buffer.subarray(0, end).toString('utf8');
    return end < buffer.length ? clipped + resultNotice : clipped;
  };
  for (const stream of [process.stdout, process.stderr]) {
    stream.write = (chunk, encoding, callback) => {
      const cell = cells.getStore();
      if (cell && !cell.done) {
        const buffer = asUtf8(chunk);
        let dropped;
        if (cell.bytes < limit) {
          const end = clipBytes(buffer, limit - cell.bytes);
          cell.bytes += end;
          emit({ id: cell.id, output: buffer.subarray(0, end).toString('utf8') });
          dropped = end < buffer.length;
        } else {
          dropped = buffer.length > 0;
        }
        // Announce the first dropped byte only: an output that exactly fills
        // the budget has not been truncated.
        if (dropped && !cell.truncated) {
          cell.truncated = true;
          emit({ id: cell.id, output: streamNotice });
        }
      }
      const cb = typeof encoding === 'function' ? encoding : callback;
      if (cb) queueMicrotask(cb);
      return true;
    };
  }
  globalThis.require = createRequire(resolve(process.cwd(), '__optimus_node__.cjs'));
  // Dynamic import inside inspector expressions has no module loader callback.
  // This helper imports through the real Node module loader instead.
  globalThis.nodeImport = (specifier) => import(isAbsolute(specifier) || specifier.startsWith('./') || specifier.startsWith('../')
    ? pathToFileURL(resolve(process.cwd(), specifier)).href : specifier);
  globalThis.__optimusInspect = (value) => inspect(value, {
    colors: false, depth: 4, maxArrayLength: 100, maxStringLength: 8192, customInspect: false, getters: false,
  });
  const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
  let queue = Promise.resolve();
  input.on('line', (line) => {
    queue = queue.then(async () => {
      const request = JSON.parse(line);
      const cell = { id: request.id, bytes: 0, done: false, truncated: false };
      await cells.run(cell, async () => {
        try {
          const response = await post('Runtime.evaluate', {
            expression: request.code, replMode: true, awaitPromise: true, objectGroup: 'optimus-cell',
          });
          let text = '';
          if (response.exceptionDetails) {
            text = response.exceptionDetails.exception?.description || response.exceptionDetails.text;
          } else if (response.result.type !== 'undefined') {
            if (response.result.objectId) {
              const formatted = await post('Runtime.callFunctionOn', {
                objectId: response.result.objectId,
                functionDeclaration: 'function() { return globalThis.__optimusInspect(this); }', returnByValue: true,
              });
              text = formatted.result.value || response.result.description || '';
            } else {
              text = response.result.unserializableValue ?? inspect(response.result.value, { colors: false, maxStringLength: 8192 });
            }
          }
          emit({ id: request.id, done: true, error: !!response.exceptionDetails, result: clipText(text) });
        } catch (error) {
          emit({ id: request.id, done: true, error: true, result: clipText(String(error)) });
        } finally {
          cell.done = true;
          await post('Runtime.releaseObjectGroup', { objectGroup: 'optimus-cell' });
          await post('Runtime.discardConsoleEntries', {});
        }
      });
    }).catch((error) => { emit({ fatal: String(error) }); process.exitCode = 1; input.close(); });
  });
  input.on('close', () => process.exit());
  emit({ ready: true, version: process.versions.node });
})();
