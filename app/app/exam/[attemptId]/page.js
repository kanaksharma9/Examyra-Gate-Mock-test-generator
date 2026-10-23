"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, CircleHelp, FileText, Info, Save, Send, X } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { AuthGuard } from "../../../components/AuthGuard";
import { Markdown } from "../../../components/Markdown";
import { Calculator } from "../../../components/exam/Calculator";
import { Palette } from "../../../components/exam/Palette";
import { get, post, put } from "../../../lib/client";
import { formatTime, nextState, serverSecondsLeft, states } from "../../../lib/exam-state";

export default function Exam() {
  return <AuthGuard><ExamContent /></AuthGuard>;
}

function ExamContent() {
  const { attemptId } = useParams();
  const router = useRouter();
  const [data, setData] = useState();
  const [current, setCurrent] = useState(0);
  const [answers, setAnswers] = useState({});
  const [palette, setPalette] = useState({});
  const [left, setLeft] = useState(0);
  const [serverOffset, setServerOffset] = useState(0);
  const [saving, setSaving] = useState("Saved");
  const [collapsed, setCollapsed] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [help, setHelp] = useState(false);
  const [paperModal, setPaperModal] = useState(false);
  const [section, setSection] = useState("GA");

  useEffect(() => {
    get(`/api/attempts/${attemptId}`).then((payload) => {
      setData(payload);
      const saved = JSON.parse(localStorage.getItem(`exam:${attemptId}`) || "{}");
      setAnswers(saved.answers || Object.fromEntries((payload.questions || []).filter((question) => question.response !== null).map((question) => [question.question_id, question.response])));
      setPalette(saved.palette || {});
      const offset = payload.server_now ? Date.parse(payload.server_now) - Date.now() : 0;
      setServerOffset(offset);
      const duration = Math.max(0, Math.ceil((Date.parse(payload.attempt.deadline_at) - Date.parse(payload.attempt.started_at)) / 1000));
      setLeft(Math.min(duration || Number.MAX_SAFE_INTEGER, Math.ceil(serverSecondsLeft(payload.attempt.deadline_at, offset))));
    }).catch((error) => {
      if (error.code === "attempt_closed") router.push(`/result/${attemptId}`);
    });
  }, [attemptId, router]);

  const questions = data?.questions || [];
  const question = questions[current];
  const hasAnswer = (id) => answers[id] !== undefined && answers[id] !== null && answers[id] !== "" && (Array.isArray(answers[id]) ? answers[id].length > 0 : true);

  useEffect(() => {
    if (!data) return undefined;
    const timer = setInterval(() => {
      const next = serverSecondsLeft(data.attempt.deadline_at, serverOffset);
      setLeft(next);
      if (next === 0) {
        clearInterval(timer);
        submit();
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [data, serverOffset]);

  const persist = useCallback((nextAnswers, nextPalette) => {
    localStorage.setItem(`exam:${attemptId}`, JSON.stringify({ answers: nextAnswers, palette: nextPalette }));
  }, [attemptId]);

  const save = useCallback(async (items) => {
    if (!items.length) return;
    setSaving("Saving…");
    try {
      await put(`/api/attempts/${attemptId}/answers`, { items });
      setSaving("Saved ✓");
    } catch {
      setSaving("Offline – will sync");
    }
  }, [attemptId]);

  const sectionIndexes = useMemo(() => questions.map((item, index) => ({ item, index })).filter(({ item }) => section === "all" || item.section === section), [questions, section]);
  const sectionPosition = sectionIndexes.findIndex(({ index }) => index === current);
  const counts = useMemo(() => {
    const values = questions.map((item) => palette[item.question_id] || states.not_visited);
    return {
      answered: values.filter((value) => value === states.answered).length,
      notAnswered: values.filter((value) => value === states.not_answered).length,
      notVisited: values.filter((value) => value === states.not_visited).length,
      marked: values.filter((value) => value === states.marked).length,
      answeredMarked: values.filter((value) => value === states.answered_marked).length,
    };
  }, [palette, questions]);

  function changeSection(nextSection) {
    setSection(nextSection);
    const candidates = questions.map((item, index) => ({ item, index })).filter(({ item }) => nextSection === "all" || item.section === nextSection);
    setCurrent((candidates.find(({ item }) => !hasAnswer(item.question_id)) || candidates[0])?.index || 0);
  }

  function go(index, paletteToUse = palette) {
    if (!questions[index]) return;
    const nextPalette = { ...paletteToUse, [question.question_id]: paletteToUse[question.question_id] || states.not_answered };
    setPalette(nextPalette);
    persist(answers, nextPalette);
    setCurrent(Math.max(0, Math.min(questions.length - 1, index)));
  }

  function updateAnswer(value) {
    const nextAnswers = { ...answers, [question.question_id]: value };
    const nextPalette = { ...palette, [question.question_id]: nextState(palette[question.question_id] || states.not_answered, "save", value !== null && value !== "" && (Array.isArray(value) ? value.length > 0 : true)) };
    setAnswers(nextAnswers);
    setPalette(nextPalette);
    persist(nextAnswers, nextPalette);
    save([{ question_id: question.question_id, response: value, state: nextPalette[question.question_id], time_spent_sec: 0, client_seq: Date.now() }]);
  }

  function action(kind) {
    const currentAnswer = answers[question.question_id];
    const nextPalette = { ...palette, [question.question_id]: nextState(palette[question.question_id] || states.not_answered, kind, hasAnswer(question.question_id)) };
    if (kind === "clear") {
      const nextAnswers = { ...answers, [question.question_id]: null };
      setAnswers(nextAnswers);
      setPalette(nextPalette);
      persist(nextAnswers, nextPalette);
      save([{ question_id: question.question_id, response: null, state: nextPalette[question.question_id], client_seq: Date.now() }]);
      return;
    }
    setPalette(nextPalette);
    persist(answers, nextPalette);
    save([{ question_id: question.question_id, response: currentAnswer ?? null, state: nextPalette[question.question_id], client_seq: Date.now() }]);
    go(current + 1, nextPalette);
  }

  async function submit() {
    try {
      await save(questions.map((item) => ({ question_id: item.question_id, response: answers[item.question_id] ?? null, state: palette[item.question_id] || states.not_visited, client_seq: Date.now() })));
      await post(`/api/attempts/${attemptId}/submit`);
      router.push(`/result/${attemptId}`);
    } catch {}
  }

  useEffect(() => {
    function key(event) {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return;
      if (event.key === "?") setHelp(true);
      if (event.key === "ArrowRight") go(current + 1);
      if (event.key === "ArrowLeft") go(current - 1);
      if (event.key.toLowerCase() === "s") action("save");
      if (event.key.toLowerCase() === "m") action("mark");
      if (event.key.toLowerCase() === "c") action("clear");
      if (["1", "2", "3", "4"].includes(event.key) && question?.type !== "NAT") {
        const keyName = ["A", "B", "C", "D"][Number(event.key) - 1];
        if (question.type === "MCQ") updateAnswer([keyName]);
        else updateAnswer((answers[question.question_id] || []).includes(keyName) ? answers[question.question_id].filter((item) => item !== keyName) : [...(answers[question.question_id] || []), keyName]);
      }
      if (/^[a-d]$/i.test(event.key) && question?.type !== "NAT") {
        const keyName = event.key.toUpperCase();
        if (question.type === "MCQ") updateAnswer([keyName]);
        else updateAnswer((answers[question.question_id] || []).includes(keyName) ? answers[question.question_id].filter((item) => item !== keyName) : [...(answers[question.question_id] || []), keyName]);
      }
    }
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [current, answers, palette, question]);

  if (!data || !question) return <div className="grid min-h-screen place-items-center">Loading exam…</div>;
  const options = question.options || {};
  const currentSectionQuestions = sectionIndexes.map(({ item }) => item);

  return (
    <div className="gate-exam-shell">
      <header className="gate-topbar">
        <div className="gate-topbar-title">{data.attempt.title}</div>
        <div className="gate-topbar-actions">
          <button type="button" onClick={() => setHelp(true)}><CircleHelp size={16} /> Shortcuts</button>
          <button type="button" onClick={() => setPaperModal(true)}><FileText size={16} /> Question Paper</button>
          <span className={`gate-timer ${left < 120 ? "danger" : left < 600 ? "warning" : ""}`}>Time Left : {formatTime(left)}</span>
        </div>
      </header>

      <div className="gate-exam-layout">
        <main className="gate-exam-main">
          <div className="gate-section-tabs">
            <span className="gate-section-label">Sections</span>
            <button type="button" onClick={() => changeSection("GA")} className={section === "GA" ? "active" : ""}>General Aptitude <Info size={15} /></button>
            <button type="button" onClick={() => changeSection("CS")} className={section === "CS" ? "active" : ""}>Computer Science <Info size={15} /></button>
          </div>
          <div className="gate-question-meta">
            <span>Question Type: <b>{question.type}</b></span>
            <span>Marks for correct answer: <b>{question.marks}</b> <em>Negative Marks: {question.type === "MCQ" ? `${question.marks}/3` : "0"}</em></span>
            <Calculator />
          </div>
          <section className="gate-question-card">
            <div className="gate-question-heading">
              <h1>Question No. {current + 1}</h1>
              <span className="gate-save-status">{saving}</span>
            </div>
            <div className="gate-question-content">
              <Markdown>{question.body_md}</Markdown>
              {question.type === "NAT" ? (
                <div className="gate-nat-answer">
                  <label htmlFor="nat-answer">Enter your answer</label>
                  <input id="nat-answer" value={answers[question.question_id] || ""} onChange={(event) => updateAnswer(event.target.value)} inputMode="decimal" placeholder="Numeric answer" />
                  <div className="gate-nat-keypad">{["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", ".", "−", "Backspace", "Clear"].map((key) => <button type="button" key={key} onClick={() => key === "Clear" ? updateAnswer("") : key === "Backspace" ? updateAnswer(String(answers[question.question_id] || "").slice(0, -1)) : updateAnswer(`${answers[question.question_id] || ""}${key === "−" ? "-" : key}`)}>{key}</button>)}</div>
                </div>
              ) : (
                <div className="gate-options">
                  {Object.entries(options).map(([key, text]) => (
                    <label key={key} className="gate-option">
                      <input type={question.type === "MCQ" ? "radio" : "checkbox"} checked={question.type === "MCQ" ? answers[question.question_id]?.[0] === key : (answers[question.question_id] || []).includes(key)} onChange={() => question.type === "MCQ" ? updateAnswer([key]) : updateAnswer((answers[question.question_id] || []).includes(key) ? answers[question.question_id].filter((item) => item !== key) : [...(answers[question.question_id] || []), key])} />
                      <span className="gate-option-marker">{key}</span>
                      <Markdown>{text}</Markdown>
                    </label>
                  ))}
                </div>
              )}
            </div>
          </section>
          <div className="gate-actionbar">
            <button type="button" onClick={() => action("mark")} className="review-button">Mark for Review &amp; Next</button>
            <button type="button" onClick={() => action("clear")} className="clear-button">Clear Response</button>
            <button type="button" onClick={() => action("save")} className="save-button"><Save size={16} /> Save &amp; Next</button>
          </div>
        </main>

        <aside className={`gate-right-panel ${collapsed ? "collapsed" : ""}`}>
          <button type="button" aria-label={collapsed ? "Expand question palette" : "Collapse question palette"} onClick={() => setCollapsed((value) => !value)} className="gate-collapse-tab">{collapsed ? <ChevronLeft size={17} /> : <ChevronRight size={17} />}</button>
          {!collapsed && (
            <>
              <div className="gate-candidate"><div className="gate-avatar">👤</div><div><span>Candidate Name</span><b>Mock Candidate</b></div></div>
              <div className="gate-legend">
                <Legend color="answered" count={counts.answered} label="Answered" />
                <Legend color="not_answered" count={counts.notAnswered} label="Not Answered" />
                <Legend color="not_visited" count={counts.notVisited} label="Not Visited" />
                <Legend color="marked" count={counts.marked} label="Marked for Review" />
                <Legend color="answered_marked" count={counts.answeredMarked} label="Answered & Marked for Review" dot />
              </div>
              <div className="gate-palette-title">{section === "CS" ? "Computer Science" : "General Aptitude"}</div>
              <div className="gate-palette-body">
                <h2>Choose a Question</h2>
                <Palette questions={currentSectionQuestions} states={palette} current={sectionPosition} onSelect={(position) => go(sectionIndexes[position].index)} />
              </div>
              <button type="button" onClick={() => setConfirm(true)} className="gate-submit-button"><Send size={16} /> Submit</button>
            </>
          )}
        </aside>
      </div>

      {confirm && <Dialog title="Submit your paper?" onClose={() => setConfirm(false)}><p className="text-sm text-slate-600">Review your question summary before submitting. Your answers will be evaluated exactly as saved.</p><div className="gate-summary-grid"><Summary label="Answered" value={counts.answered + counts.answeredMarked} /><Summary label="Not Answered" value={counts.notAnswered} /><Summary label="Marked" value={counts.marked + counts.answeredMarked} /><Summary label="Not Visited" value={counts.notVisited} /></div><div className="gate-dialog-actions"><button type="button" onClick={() => setConfirm(false)} className="clear-button">Continue exam</button><button type="button" onClick={submit} className="save-button">Submit now</button></div></Dialog>}
      {help && <Dialog title="Keyboard shortcuts" onClose={() => setHelp(false)}><div className="shortcut-list"><Shortcut keys="← / →" label="Previous / next question" /><Shortcut keys="S" label="Save & Next" /><Shortcut keys="M" label="Mark for Review & Next" /><Shortcut keys="C" label="Clear response" /><Shortcut keys="1–4 / A–D" label="Select an option" /><Shortcut keys="?" label="Open this help" /></div></Dialog>}
      {paperModal && <Dialog title="Question paper" onClose={() => setPaperModal(false)}><div className="gate-paper-list">{questions.map((item, index) => <button type="button" key={item.question_id} onClick={() => { setPaperModal(false); go(index); }}><b>{index + 1}</b><span>{item.type} · {item.marks} marks</span></button>)}</div></Dialog>}
    </div>
  );
}

function Legend({ color, count, label, dot }) {
  return <div className="gate-legend-item"><span className={`gate-legend-icon ${color}`}>{count}{dot && <i />}</span><span>{label}</span></div>;
}
function Summary({ label, value }) { return <div><b>{value}</b><span>{label}</span></div>; }
function Shortcut({ keys, label }) { return <div><kbd>{keys}</kbd><span>{label}</span></div>; }
function Dialog({ title, onClose, children }) { return <div className="gate-modal-backdrop"><div className="gate-dialog" role="dialog" aria-modal="true"><div className="gate-dialog-title"><h2>{title}</h2><button type="button" aria-label="Close dialog" onClick={onClose}><X size={19} /></button></div>{children}</div></div>; }
