import { describe, expect, it } from "vitest";
import { nextState, states, formatTime, serverSecondsLeft } from "../lib/exam-state";
describe("exam palette state machine",()=>{it("visits and answers",()=>{expect(nextState(states.not_visited,"open",false)).toBe(states.not_answered);expect(nextState(states.not_answered,"save",true)).toBe(states.answered)});it("keeps review when cleared",()=>expect(nextState(states.answered_marked,"clear",false)).toBe(states.marked));it("formats timer",()=>expect(formatTime(3661)).toBe("01:01:01"))});
describe("server timer",()=>it("uses server offset instead of local countdown",()=>expect(serverSecondsLeft("2026-01-01T00:00:10Z",2000,Date.parse("2026-01-01T00:00:00Z"))).toBe(8)));
