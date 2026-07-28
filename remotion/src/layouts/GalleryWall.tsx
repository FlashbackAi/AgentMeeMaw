import React from "react";
import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { LayoutProps } from "../theme";
import { pop, ramp } from "../anim";
import { useFit } from "../fit";
import { Grain, LightLeak, Vignette } from "../FX";

// Framed paintings on a quiet gallery wall, the camera drifting past; a small
// brass plaque carries the line. Calm and stately.
const Framed: React.FC<{ image: string; top: string; left: string; w: number; h: number; t: number }> = ({ image, top, left, w, h, t }) => (
  <div
    style={{
      position: "absolute", top, left, width: w, height: h,
      border: "20px solid #3b2f22", background: "#f7f2e7", padding: 22,
      boxShadow: "0 24px 55px rgba(0,0,0,.35)",
      opacity: t, transform: `translateY(${(1 - t) * 60}px)`,
    }}
  >
    <Img src={staticFile(image)} style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }} />
  </div>
);

export const GalleryWall: React.FC<LayoutProps> = ({ text, image, image2, recipe }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t1 = ramp(frame, 2, 22);
  const t2 = ramp(frame, 12, 32);
  const plaque = pop(frame, fps, 26, 13);
  const pan = interpolate(frame, [0, 150], [16, -16]);
  const size = useFit({
    text,
    font: (s) => `400 ${s}px "${recipe.fonts.eyebrow_family ?? "EB Garamond"}", serif`,
    maxWidth: 896 * 0.78 - 88,
    maxHeight: 1600 * 0.17,
    maxSize: 38,
    minSize: 20,
    lineHeight: 1.25,
    trackingEm: 3 / 38,
  });
  return (
    <AbsoluteFill style={{ backgroundColor: "#e6dccb" }}>
      <div style={{ position: "absolute", inset: 0, background: "linear-gradient(to bottom, rgba(255,250,235,.5), transparent 40%, rgba(70,58,40,.18))" }} />
      <LightLeak hue="#ffe2ae" strength={0.09} />
      <div style={{ position: "absolute", inset: 0, transform: `translateX(${pan}px)` }}>
        <Framed image={image} top="9%" left="8%" w={560} h={740} t={t1} />
        <Framed image={image2 ?? image} top="44%" left="45%" w={400} h={520} t={t2} />
      </div>
      <div
        style={{
          position: "absolute", left: "50%", bottom: "9%",
          transform: `translateX(-50%) scale(${0.7 + plaque * 0.3})`, opacity: plaque,
          background: "linear-gradient(160deg, #c9a86a, #a5804a)", borderRadius: 8,
          padding: "22px 44px", boxShadow: "0 10px 26px rgba(0,0,0,.3)", maxWidth: "78%",
        }}
      >
        <span style={{ fontFamily: recipe.fonts.eyebrow_family ?? "EB Garamond", fontSize: size, lineHeight: 1.25, letterSpacing: 3, color: "#2e2416", textAlign: "center", display: "block" }}>
          {text}
        </span>
      </div>
      <Vignette strength={0.3} />
      <Grain opacity={0.05} />
    </AbsoluteFill>
  );
};
