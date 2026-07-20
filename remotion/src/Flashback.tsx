import React, { useEffect, useState } from "react";
import { AbsoluteFill, continueRender, delayRender, useVideoConfig } from "remotion";
import { TransitionSeries, linearTiming, springTiming } from "@remotion/transitions";
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
  // Ordered so the first three (the ones a 4-scene film uses) are maximally
  // distinct: a spring slide, a clock wipe, then a flip.
  const TRANSITIONS = [
    { presentation: () => slide({ direction: "from-right" }), timing: (f: number) => springTiming({ config: { damping: 200 }, durationInFrames: f }) },
    { presentation: () => clockWipe({ width, height }), timing: (f: number) => linearTiming({ durationInFrames: f }) },
    { presentation: () => flip(), timing: (f: number) => springTiming({ config: { damping: 200 }, durationInFrames: f }) },
    { presentation: () => wipe({ direction: "from-bottom-right" }), timing: (f: number) => linearTiming({ durationInFrames: f }) },
    { presentation: () => fade(), timing: (f: number) => linearTiming({ durationInFrames: f }) },
  ];
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
              <Comp text={s.text} image={s.image} image2={s.image2} recipe={recipe} />
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
