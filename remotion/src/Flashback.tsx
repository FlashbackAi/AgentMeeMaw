import React, { useEffect, useState } from "react";
import { AbsoluteFill, continueRender, delayRender, useVideoConfig } from "remotion";
import { TransitionSeries, linearTiming, springTiming } from "@remotion/transitions";
import type { TransitionPresentation } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { slide } from "@remotion/transitions/slide";
import { wipe } from "@remotion/transitions/wipe";
import { clockWipe } from "@remotion/transitions/clock-wipe";
import { flip } from "@remotion/transitions/flip";
import { LAYOUTS, DEFAULT_LAYOUT } from "./layouts/registry";
import { FlashbackProps } from "./theme";

export const Flashback: React.FC<FlashbackProps> = ({ recipe, scenes }) => {
  const { fps, width, height } = useVideoConfig();

  // Rotating transition vocabulary so cuts never feel flat. Built here because
  // clockWipe needs the composition dimensions.
  // The motion preset (recipe.motion_preset) picks the transition vocabulary:
  // punchy = fast spring cuts, playful = bouncy, cinematic = smooth wipes,
  // calm = a single gentle fade (memorial). Empty -> punchy (Friendship default).
  const springT = (f: number) => springTiming({ config: { damping: 200 }, durationInFrames: f });
  const bouncyT = (f: number) => springTiming({ config: { damping: 11 }, durationInFrames: f });
  const linT = (f: number) => linearTiming({ durationInFrames: f });
  // Presentations are widened to a common generic — each entry carries its own
  // props type and the union doesn't unify at the JSX prop otherwise.
  type AnyPresentation = TransitionPresentation<Record<string, unknown>>;
  const widen = (p: unknown) => p as AnyPresentation;
  const TX: Record<string, { presentation: () => AnyPresentation; timing: (f: number) => ReturnType<typeof linearTiming> }> = {
    slide: { presentation: () => widen(slide({ direction: "from-right" })), timing: springT },
    clock: { presentation: () => widen(clockWipe({ width, height })), timing: linT },
    flip: { presentation: () => widen(flip()), timing: bouncyT },
    wipe: { presentation: () => widen(wipe({ direction: "from-bottom-right" })), timing: linT },
    fade: { presentation: () => widen(fade()), timing: linT },
  };
  const SETS: Record<string, Array<keyof typeof TX>> = {
    punchy: ["slide", "clock", "flip"],
    playful: ["flip", "slide", "fade"],
    cinematic: ["wipe", "clock", "fade"],
    calm: ["fade"],
  };
  const preset = recipe.motion_preset || "punchy";
  const TRANSITIONS = (SETS[preset] ?? SETS.punchy).map((k) => TX[k]);
  const [handle] = useState(() => delayRender("load-fonts"));

  useEffect(() => {
    const fonts = (document as unknown as { fonts: FontFaceSet }).fonts;
    Promise.all([
      fonts.load("italic 60px 'Playfair Display'"),
      fonts.load("900 132px 'Nunito'"),
      fonts.load("800 100px 'Nunito'"),
      fonts.load("400 96px 'Caveat'"),
    ])
      .then(() => continueRender(handle))
      .catch(() => continueRender(handle));
  }, [handle]);

  const hold = Math.max(1, Math.round((recipe.pacing?.hold ?? 2.4) * fps));
  const trans = Math.max(1, Math.round((recipe.pacing?.transition ?? 0.6) * fps));

  return (
    <AbsoluteFill>
      <TransitionSeries>
        {scenes.flatMap((s, i) => {
          const Comp = LAYOUTS[s.layout_slug] ?? LAYOUTS[DEFAULT_LAYOUT];
          const seq = (
            <TransitionSeries.Sequence key={`s${i}`} durationInFrames={hold}>
              <Comp text={s.text} display={s.display} image={s.image} image2={s.image2} recipe={recipe} />
            </TransitionSeries.Sequence>
          );
          if (i === 0) return [seq];
          const tr = TRANSITIONS[(i - 1) % TRANSITIONS.length];
          return [
            <TransitionSeries.Transition key={`t${i}`} timing={tr.timing(trans)} presentation={tr.presentation()} />,
            seq,
          ];
        })}
      </TransitionSeries>
    </AbsoluteFill>
  );
};
