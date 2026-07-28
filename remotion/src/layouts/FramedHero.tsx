import React from "react";
import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { LayoutProps } from "../theme";
import { useFit } from "../fit";

// The calm default (memorial). One hero, framed, text above. Fallback layout.
export const FramedHero: React.FC<LayoutProps> = ({ text, image, recipe }) => {
  const frame = useCurrentFrame();
  const op = interpolate(frame, [0, 14], [0, 1], { extrapolateRight: "clamp" });
  const size = useFit({
    text,
    font: (s) => `italic 400 ${s}px "${recipe.fonts.main_family}", serif`,
    maxWidth: 896 * 0.78,
    maxHeight: 1600 * 0.24,
    maxSize: 72,
    minSize: 30,
    lineHeight: 1.15,
  });
  return (
    <AbsoluteFill style={{ backgroundColor: "#f3ead7", padding: 48 }}>
      <AbsoluteFill style={{ opacity: op }}>
        <div style={{ height: "26%", display: "flex", alignItems: "center", justifyContent: "center", padding: "0 8%" }}>
          <span style={{ fontFamily: recipe.fonts.main_family, fontStyle: "italic", fontSize: size, lineHeight: 1.15, color: recipe.ink.main_fill, textAlign: "center" }}>
            {text}
          </span>
        </div>
        <Img
          src={staticFile(image)}
          style={{ position: "absolute", top: "30%", left: "6%", width: "88%", height: "62%", objectFit: "cover", borderRadius: 10 }}
        />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
