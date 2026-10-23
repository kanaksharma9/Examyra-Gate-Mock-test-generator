"use client";

import { memo } from "react";

const styles = {
  not_visited: "bg-[#eef0f2] text-[#3d4650] border-[#b7bdc4]",
  not_answered: "bg-[#ef641e] text-white border-[#c74b0d]",
  answered: "bg-[#65b82e] text-white border-[#4b951c]",
  marked: "bg-[#7951a9] text-white border-[#5b3788]",
  answered_marked: "bg-[#7951a9] text-white border-[#5b3788] ring-2 ring-inset ring-[#8ed33f]",
};

export const Palette = memo(function Palette({ questions, states, current, onSelect }) {
  return (
    <div className="gate-palette-grid">
      {questions.map((question, index) => {
        const state = states[question.question_id] || "not_visited";
        return (
          <button
            type="button"
            key={question.question_id}
            onClick={() => onSelect(index)}
            aria-label={`Question ${index + 1}, ${state.replaceAll("_", " ")}`}
            className={`gate-palette-tile ${styles[state]} ${
              index === current ? "gate-palette-current" : ""
            }`}
          >
            {index + 1}
            {state === "answered_marked" && <span className="gate-palette-dot" />}
          </button>
        );
      })}
    </div>
  );
});
