import React from "react";
import { useCurrentFrame } from "remotion";
import { ramp } from "./anim";

// Each word rises into view from behind a mask, staggered. The overflow-hidden
// wrapper is the mask; the inner span slides up under it. Reads as "printed on".
// block mode (default): one word per line — for stacked headlines.
// inline mode: words flow and wrap — for captions/sentences.
export const KineticWords: React.FC<{
  text: string;
  style?: React.CSSProperties;
  stagger?: number;
  delay?: number;
  up?: number;
  scaleFrom?: number;
  inline?: boolean;
}> = ({ text, style, stagger = 4, delay = 0, up = 120, scaleFrom = 1, inline = false }) => {
  const frame = useCurrentFrame();
  // The mask clips BELOW the line box only. `overflow: hidden` used to clip all
  // four sides, so a word wider than its column was chopped mid-letter with no
  // visual cue ("WRONG" -> "WRO"). Layouts fit their own type now, and this
  // keeps a near-miss looking like a near-miss instead of a truncation.
  const mask = "inset(-45% -100% 0% -100%)";
  // The inter-word gap is em-based, so the wrapper needs the same font-size as
  // the inner span -- otherwise it resolves against the inherited 16px root and
  // every caption renders with its words jammed together.
  const em: React.CSSProperties = { fontSize: style?.fontSize, lineHeight: style?.lineHeight };
  const wrap: React.CSSProperties = inline
    ? { ...em, display: "inline-block", clipPath: mask, verticalAlign: "bottom", marginRight: "0.28em", paddingBottom: "0.06em" }
    : { ...em, display: "block", clipPath: mask, paddingBottom: "0.06em" };
  return (
    <>
      {text.split(" ").map((w, i) => {
        const t = ramp(frame, delay + i * stagger, delay + i * stagger + 20);
        const scale = scaleFrom + (1 - scaleFrom) * t;
        return (
          <span key={i} style={wrap}>
            <span
              style={{
                display: "inline-block",
                transform: `translateY(${(1 - t) * up}px) scale(${scale})`,
                transformOrigin: "left bottom",
                opacity: t,
                ...style,
              }}
            >
              {w}
            </span>
          </span>
        );
      })}
    </>
  );
};
