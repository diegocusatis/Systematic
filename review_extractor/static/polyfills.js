// Map/WeakMap "upsert" methods used by the bundled PDF.js. Older Chrome, Edge,
// Firefox and Safari versions lack them, which stops the PDF viewer from loading.
for (const type of [Map, WeakMap]) {
  const proto = type.prototype;
  if (!proto.getOrInsert) {
    Object.defineProperty(proto, 'getOrInsert', {configurable: true, writable: true, value(key, value) {
      if (!this.has(key)) this.set(key, value);
      return this.get(key);
    }});
  }
  if (!proto.getOrInsertComputed) {
    Object.defineProperty(proto, 'getOrInsertComputed', {configurable: true, writable: true, value(key, compute) {
      if (!this.has(key)) this.set(key, compute(key));
      return this.get(key);
    }});
  }
}
