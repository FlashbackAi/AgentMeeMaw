import React from "react";
import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { LayoutProps } from "../theme";
import { drift, ramp } from "../anim";
import { KineticWords } from "../Kinetic";
import { useFit } from "../fit";
import { Grain, LightLeak } from "../FX";

// Art one side; a bold colour block wipes in over the other, an accent rule
// draws down the seam, then the title rises word-by-word. "HOW WE MET".

// The colour block is 46% of the 896px frame. Its inset is stated in px, not
// %: percentage padding on an absolutely-positioned box resolves against the
// containing block (the 896px frame), so "9%" was silently 81px a side, not 37
// -- and the fitted column came out 87px too wide.
const BLOCK_W = 896 * 0.46;
const PAD = 40;
const COL_W = BLOCK_W - PAD * 2;

export const SplitDuotone: React.FC<LayoutProps> = ({ text, display, image, recipe }) => {
  const frame = useCurrentFrame();
  const title = display || text;
  const words = title.toUpperCase();
  const size = useFit({
    text: words,
    font: (s) => `800 ${s}px "${recipe.fonts.display_family ?? "Nunito"}", sans-serif`,
    maxWidth: COL_W,
    maxHeight: 1600 * 0.62,
    maxSize: 96,
    minSize: 30,
    perWord: true,
    lineHeight: 0.98,
  });
  const chapter = recipe.labels?.chapter ?? "";
  const blockIn = ramp(frame, 0, 20);
  const rule = ramp(frame, 16, 34);
  const parallax = interpolate(frame, [0, 90], [0, -26]);
  return (
    <AbsoluteFill style={{ backgroundColor: "#f3ead7" }}>
      <div style={{ position: "absolute", left: 0, top: 0, width: "56%", height: "100%", overflow: "hidden" }}>
        <Img
          src={staticFile(image)}
          style={{ width: "118%", height: "100%", objectFit: "cover", transform: `translateX(${parallax}px) scale(${drift(frame, 1.1)})` }}
        />
        <LightLeak hue="#fff0c8" strength={0.12} />
      </div>
      <div
        style={{
          position: "absolute", right: 0, top: 0, width: "46%", height: "100%",
          backgroundColor: recipe.ink.accent ?? "#e8552e",
          transform: `translateX(${(1 - blockIn) * 100}%)`,
          display: "flex", flexDirection: "column", justifyContent: "center", padding: `0 ${PAD}px`,
        }}
      >
        <div style={{ position: "absolute", left: 0, top: "50%", width: 8, height: `${rule * 46}%`, transform: "translateY(-50%)", background: "rgba(255,255,255,0.85)" }} />
        <div style={{ transform: `translateY(${interpolate(frame, [0, 120], [0, -10])}px)` }}>
          {chapter ? (
            <span style={{ display: "block", fontFamily: recipe.fonts.eyebrow_family ?? "EB Garamond", color: "rgba(255,255,255,0.85)", letterSpacing: 6, fontSize: 26, textTransform: "uppercase", opacity: ramp(frame, 20, 34), marginBottom: 14 }}>
              {chapter}
            </span>
          ) : null}
          <KineticWords
            text={words}
            delay={12}
            stagger={5}
            style={{ color: "#fff", fontFamily: recipe.fonts.display_family ?? "Nunito", fontWeight: 800, fontSize: size, lineHeight: 0.98, letterSpacing: -1 }}
          />
        </div>
      </div>
      <Grain opacity={0.05} />
    </AbsoluteFill>
  );
};
