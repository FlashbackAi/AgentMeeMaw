import React from "react";
import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { LayoutProps } from "../theme";
import { drift, ramp } from "../anim";
import { KineticWords } from "../Kinetic";
import { useFit } from "../fit";
import { Grain, Vignette } from "../FX";

// The art shows through one giant word (the first word of the line), then the
// full caption settles beneath. Punchy, modern — the campaign register.
export const WordMask: React.FC<LayoutProps> = ({ text, display, image, recipe }) => {
  const frame = useCurrentFrame();
  // Hero the display title's strongest (first) word; the full line settles
  // beneath. Without a display, fall back to the line's own first word.
  const hero = ((display || text).trim().split(/\s+/)[0] ?? "US").toUpperCase();
  const rest = display ? text : text.trim().split(/\s+/).slice(1).join(" ");
  const settle = ramp(frame, 0, 26);
  const dim = ramp(frame, 34, 56);
  // The old estimate (1350 / length) assumed a narrower face than Nunito 900
  // and let long words bleed off both edges; measure instead. The word also
  // scales up 1.25x on entry, so fit inside that headroom.
  const heroSize = useFit({
    text: hero,
    font: (s) => `900 ${s}px "${recipe.fonts.display_family ?? "Nunito"}", sans-serif`,
    maxWidth: (896 * 0.9) / 1.25,
    maxSize: 300,
    minSize: 46,
    perWord: true,
    lineHeight: 0.95,
    trackingEm: -4 / 300,
  });
  const restSize = useFit({
    text: rest || " ",
    font: (s) => `italic 400 ${s}px "${recipe.fonts.main_family}", serif`,
    maxWidth: 896 * 0.86,
    maxHeight: 1600 * 0.26,
    maxSize: 58,
    minSize: 26,
    lineHeight: 1.2,
  });
  return (
    <AbsoluteFill style={{ backgroundColor: "#0d0b09" }}>
      <Img
        src={staticFile(image)}
        style={{
          width: "100%", height: "100%", objectFit: "cover",
          transform: `scale(${drift(frame, 1.1, 0.0008)})`,
          filter: "brightness(0.32)", opacity: interpolate(dim, [0, 1], [0, 1]),
        }}
      />
      <AbsoluteFill style={{ display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", padding: "0 5%" }}>
        <span
          style={{
            fontFamily: recipe.fonts.display_family ?? "Nunito", fontWeight: 900,
            fontSize: heroSize, lineHeight: 0.95, letterSpacing: -4, textAlign: "center",
            backgroundImage: `url(${staticFile(image)})`, backgroundSize: "cover", backgroundPosition: "center",
            WebkitBackgroundClip: "text", backgroundClip: "text", color: "transparent",
            transform: `scale(${1.25 - settle * 0.25})`,
            filter: "contrast(1.1) saturate(1.15)",
          }}
        >
          {hero}
        </span>
        {rest && (
          <div style={{ marginTop: 30, textAlign: "center", maxWidth: "90%" }}>
            <KineticWords
              text={rest}
              inline
              delay={30}
              stagger={3}
              up={60}
              style={{ color: "#fff", fontFamily: recipe.fonts.main_family, fontStyle: "italic", fontSize: restSize, lineHeight: 1.2, textShadow: "0 2px 20px rgba(0,0,0,.6)" }}
            />
          </div>
        )}
        <div style={{ height: 12, width: `${ramp(frame, 44, 62) * 34}%`, marginTop: 34, borderRadius: 6, background: recipe.ink.accent ?? "#e8552e" }} />
      </AbsoluteFill>
      <Vignette strength={0.45} />
      <Grain />
    </AbsoluteFill>
  );
};
