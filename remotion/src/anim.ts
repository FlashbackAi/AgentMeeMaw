// Shared motion helpers — tasteful, physical, layered.
import { Easing, interpolate, spring } from "remotion";

// easeOutExpo-ish: fast start, long graceful settle. The workhorse for reveals.
export const OUT = Easing.bezier(0.16, 1, 0.3, 1);
export const IN_OUT = Easing.bezier(0.65, 0, 0.35, 1);

// Eased 0..1 ramp between two frames.
export function ramp(frame: number, start: number, end: number, easing = OUT): number {
  return interpolate(frame, [start, end], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing,
  });
}

// Springy 0..1 with optional delay and overshoot (low damping = more bounce).
export function pop(frame: number, fps: number, delay = 0, damping = 12): number {
  return spring({ frame: frame - delay, fps, config: { damping, mass: 0.8 }, durationInFrames: 60 });
}

// Slow perpetual zoom so a scene keeps breathing after its entrance.
export function drift(frame: number, from = 1.06, per = 0.0007): number {
  return from + frame * per;
}
