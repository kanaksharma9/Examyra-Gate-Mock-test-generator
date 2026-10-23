export function makeSaveQueue(send) {
  let pending = new Map(), seq = 0, timer;
  const flush = async () => {
    if (!pending.size) return;
    const items = [...pending.values()].map((item) => ({ ...item, seq: ++seq }));
    pending.clear();
    try { await send(items); } catch { items.forEach((item) => pending.set(item.question_id, item)); throw new Error("offline"); }
  };
  return {
    enqueue(item) { pending.set(item.question_id, item); clearTimeout(timer); timer = setTimeout(() => flush().catch(() => {}), 600); },
    flush,
    get size() { return pending.size; }
  };
}
