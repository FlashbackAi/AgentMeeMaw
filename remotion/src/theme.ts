// Shared prop types — the Python side (props builder) emits exactly this shape.
export type Ink = {
  main_fill: string;
  eyebrow_fill: string;
  accent?: string;
};

export type Fonts = {
  main_family: string;      // elegant serif lines (Playfair Display italic)
  eyebrow_family: string;   // EB Garamond
  display_family?: string;  // heavy hype headlines (Nunito)
  script_family?: string;   // handwritten scrawl (Caveat)
};

export type Recipe = {
  fonts: Fonts;
  ink: Ink;
  pacing: { hold: number; transition: number };
};

export type Scene = {
  role: string;
  layout_slug: string;
  text: string;
  image: string;
  image2?: string;          // second image for multi-image layouts (scrapbook)
};

export type FlashbackProps = {
  meta: { width: number; height: number; fps: number; cover_title?: string };
  recipe: Recipe;
  scenes: Scene[];
};

export type LayoutProps = {
  text: string;
  image: string;
  image2?: string;
  recipe: Recipe;
};
