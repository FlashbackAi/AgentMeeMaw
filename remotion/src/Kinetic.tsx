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
  const wrap: React.CSSProperties = inline
    ? { display: "inline-block", overflow: "hidden", verticalAlign: "bottom", marginRight: "0.28em", paddingBottom: "0.06em" }
    : { display: "block", overflow: "hidden", paddingBottom: "0.06em" };
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
