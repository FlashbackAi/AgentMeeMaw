import React from "react";
import { AbsoluteFill, Img, staticFile, useCurrentFrame } from "remotion";
import { LayoutProps } from "../theme";
import { drift, ramp } from "../anim";
import { KineticWords } from "../Kinetic";
import { useFit } from "../fit";
import { Grain } from "../FX";

// A clean editorial spread: tall art on the right, a vertical eyebrow up the
// white margin, the serif headline breaking across the image's edge.
export const Magazine: React.FC<LayoutProps> = ({ text, image, recipe }) => {
  const frame = useCurrentFrame();
  const artIn = ramp(frame, 0, 22);
  const eyebrow = ramp(frame, 14, 34);
  const rule = ramp(frame, 20, 40);
  const accent = recipe.ink.accent ?? "#e8552e";
  const editorial = recipe.labels?.editorial ?? "";
  const size = useFit({
    text,
    font: (s) => `italic 400 ${s}px "${recipe.fonts.main_family}", serif`,
    maxWidth: 896 * 0.82,
    maxHeight: 1600 * 0.34,
    maxSize: 76,
    minSize: 34,
    lineHeight: 1.12,
  });
  return (
    <AbsoluteFill style={{ backgroundColor: "#f6f3ec" }}>
      <div style={{ position: "absolute", top: 0, right: 0, bottom: 0, width: "72%", overflow: "hidden" }}>
        <Img
          src={staticFile(image)}
          style={{
            width: "108%", height: "100%", objectFit: "cover",
            transform: `translateX(${(1 - artIn) * 8}%) scale(${drift(frame, 1.06)})`,
            filter: "saturate(0.96)",
          }}
        />
      </div>
      {editorial ? (
        <div
          style={{
            position: "absolute", left: 34, top: "8%", maxHeight: "62%", opacity: eyebrow,
            writingMode: "vertical-rl", transform: "rotate(180deg)", overflow: "hidden",
            fontFamily: recipe.fonts.eyebrow_family ?? "EB Garamond", fontSize: 30,
            letterSpacing: 10, textTransform: "uppercase", color: recipe.ink.eyebrow_fill,
          }}
        >
          {editorial}
        </div>
      ) : null}
      <div style={{ position: "absolute", left: "8%", right: "10%", bottom: "13%" }}>
        <div style={{ height: 10, width: `${rule * 20}%`, marginBottom: 26, background: accent }} />
        <KineticWords
          text={text}
          inline
          delay={10}
          stagger={4}
          up={70}
          style={{
            color: "#221b12", fontFamily: recipe.fonts.main_family, fontStyle: "italic",
            fontSize: size, lineHeight: 1.12,
            textShadow: "0 0 26px rgba(246,243,236,.85), 0 0 8px rgba(246,243,236,.9)",
          }}
        />
      </div>
      <div style={{ position: "absolute", left: 38, bottom: 40, width: 14, height: 14, borderRadius: "50%", background: accent, opacity: eyebrow }} />
      <Grain opacity={0.04} />
    </AbsoluteFill>
  );
};
