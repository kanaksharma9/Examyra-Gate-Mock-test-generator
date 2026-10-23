"use client";

import { useState } from "react";
import { Calculator as CalculatorIcon, Delete, GripHorizontal, X } from "lucide-react";

export function Calculator() {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");

  function press(key) {
    if (key === "clear") return setValue("");
    if (key === "backspace") return setValue((current) => current.slice(0, -1));
    if (key === "=") {
      try {
        const expression = value.replaceAll("×", "*").replaceAll("÷", "/");
        setValue(String(Function(`"use strict"; return (${expression})`)()));
      } catch {
        setValue("Error");
      }
      return;
    }
    setValue((current) => (current === "Error" ? key : current + key));
  }

  return (
    <div className="relative">
      <button
        type="button"
        aria-label="Open virtual calculator"
        onClick={() => setOpen((current) => !current)}
        className="gate-calculator-trigger"
      >
        <CalculatorIcon size={19} />
      </button>
      {open && (
        <div className="gate-calculator-popover" role="dialog" aria-label="Virtual calculator">
          <div className="gate-calculator-title">
            <span><GripHorizontal size={16} /> Calculator</span>
            <button type="button" aria-label="Close calculator" onClick={() => setOpen(false)}><X size={16} /></button>
          </div>
          <div className="gate-calculator-display">{value || "0"}</div>
          <div className="gate-calculator-grid">
            {["7", "8", "9", "÷", "4", "5", "6", "×", "1", "2", "3", "-", "0", ".", "%", "+", "clear", "backspace", "√", "="].map((key) => (
              <button
                type="button"
                key={key}
                onClick={() => key === "√" ? setValue((current) => `Math.sqrt(${current || 0})`) : press(key)}
                className={`gate-calculator-key ${key === "=" ? "gate-calculator-equals" : ""}`}
              >
                {key === "clear" ? <span>AC</span> : key === "backspace" ? <Delete size={15} className="mx-auto" /> : key}
              </button>
            ))}
          </div>
          <p className="gate-calculator-note">Basic calculator · no memory</p>
        </div>
      )}
    </div>
  );
}
