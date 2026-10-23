export const states = { not_visited: "not_visited", not_answered: "not_answered", answered: "answered", marked: "marked", answered_marked: "answered_marked" };
export function nextState(current, action, hasAnswer) {
  if (action === "open") return current === states.not_visited ? states.not_answered : current;
  if (action === "clear") return current === states.answered_marked ? states.marked : states.not_answered;
  if (action === "mark") return hasAnswer ? states.answered_marked : states.marked;
  if (action === "save") return hasAnswer ? states.answered : states.not_answered;
  return current;
}
export function formatTime(seconds) {
  const safe = Math.max(0, Math.floor(seconds));
  return [Math.floor(safe / 3600), Math.floor((safe % 3600) / 60), safe % 60].map((x) => String(x).padStart(2, "0")).join(":");
}
export function serverSecondsLeft(deadlineAt, serverOffset = 0, now = Date.now()) {
  return Math.max(0, Math.floor((new Date(deadlineAt).getTime() - (now + serverOffset)) / 1000));
}
