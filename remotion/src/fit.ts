// Text fitting. Every layout paints LLM-authored text of unpredictable length
// into a fixed 896x1600 frame, so no layout may hard-code a font size: a long
// word silently overflowed its box (and, inside a KineticWords mask, was
// clipped mid-letter -- "WRONG CAMPUS" rendered as "WRO CAMP").
//
// measureText on a canvas is exact and cheap, but only once the webfonts are
// live -- before that Chrome measures the fallback and we'd fit to the wrong
// metrics. So the hook holds the render open (delayRender) until
// document.fonts is ready, measures, then releases. Until then it reports the
// max size, which is what the layout used to hard-code.
import { useEffect, useState } from "react";
import { continueRender, delayRender } from "remotion";

export type FitSpec = {
  text: string;
  // CSS `font` shorthand for a given px size, e.g. (s) => `900 ${s}px Nunito`.
  font: (size: number) => string;
  maxWidth: number;
  maxSize: number;
  // Each word gets its own line (stacked kinetic headlines) -> fit the widest
  // word rather than the wrapped paragraph.
  perWord?: boolean;
  // When set, the wrapped block must also fit this height.
  maxHeight?: number;
  lineHeight?: number;
  // letter-spacing as a fraction of the font size (px value / font size).
  trackingEm?: number;
  minSize?: number;
};

const REF = 100; // measure once at 100px; text width scales linearly with size

type Ctx = CanvasRenderingContext2D;

let shared: Ctx | null = null;
function ctx(): Ctx | null {
  if (shared) return shared;
  const c = document.createElement("canvas").getContext("2d");
  shared = c;
  return c;
}

function widthAt100(c: Ctx, text: string, font: (s: number) => string, trackingEm: number): number {
  c.font = font(REF);
  return c.measureText(text).width + trackingEm * REF * text.length;
}

// Greedy wrap at `size`, returning the line count and whether any single word
// still overflows (a word can't be broken, so that's the binding constraint).
function wrap(c: Ctx, words: string[], spec: FitSpec, size: number): { lines: number; fits: boolean } {
  const scale = size / REF;
  const tracking = spec.trackingEm ?? 0;
  const space = widthAt100(c, " ", spec.font, tracking) * scale;
  let lines = 1;
  let cur = 0;
  let fits = true;
  for (const w of words) {
    const ww = widthAt100(c, w, spec.font, tracking) * scale;
    if (ww > spec.maxWidth) fits = false;
    if (cur === 0) {
      cur = ww;
    } else if (cur + space + ww <= spec.maxWidth) {
      cur += space + ww;
    } else {
      lines += 1;
      cur = ww;
    }
  }
  return { lines, fits };
}

function compute(spec: FitSpec): number {
  const c = ctx();
  if (!c) return spec.maxSize;
  const words = spec.text.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return spec.maxSize;
  const min = spec.minSize ?? 16;
  const lh = spec.lineHeight ?? 1.15;
  const tracking = spec.trackingEm ?? 0;

  if (spec.perWord) {
    // Widest word decides; every word occupies a full line.
    const widest = Math.max(...words.map((w) => widthAt100(c, w, spec.font, tracking)));
    let size = widest > 0 ? Math.floor((spec.maxWidth / widest) * REF) : spec.maxSize;
    size = Math.min(spec.maxSize, size);
    if (spec.maxHeight) {
      size = Math.min(size, Math.floor(spec.maxHeight / (words.length * lh)));
    }
    return Math.max(min, size);
  }

  // Wrapped paragraph: step down until both the width and the height hold.
  for (let size = spec.maxSize; size > min; size -= 2) {
    const { lines, fits } = wrap(c, words, spec, size);
    if (!fits) continue;
    if (!spec.maxHeight || lines * lh * size <= spec.maxHeight) return size;
  }
  return min;
}

/**
 * Largest font size (<= spec.maxSize) at which `text` fits the box.
 * Returns spec.maxSize on the first render, then the measured value.
 */
export function useFit(spec: FitSpec): number {
  const [size, setSize] = useState(spec.maxSize);
  const key = JSON.stringify([
    spec.text, spec.maxWidth, spec.maxHeight, spec.maxSize,
    spec.perWord, spec.lineHeight, spec.trackingEm, spec.minSize,
    spec.font(REF),
  ]);

  useEffect(() => {
    const handle = delayRender(`fit-text:${spec.text.slice(0, 40)}`);
    let live = true;
    const done = () => {
      if (live) {
        try {
          setSize(compute(spec));
        } catch {
          setSize(spec.maxSize);
        }
      }
      continueRender(handle);
    };
    const fonts = (document as unknown as { fonts?: FontFaceSet }).fonts;
    if (fonts?.ready) {
      fonts.ready.then(done).catch(done);
    } else {
      done();
    }
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return size;
}
