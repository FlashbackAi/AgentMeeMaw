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

// Small pieces of chrome a layout paints alongside the line. They used to be
// hard-coded per layout, which put memorial copy ("A LIFE REMEMBERED") on a
// Friendship-Day tribute; the occasion owns them now. Empty string = omit.
export type Labels = {
  chapter?: string;    // split_duotone eyebrow
  editorial?: string;  // magazine vertical eyebrow
  stamp?: string;      // postcard postmark
};

export type Recipe = {
  fonts: Fonts;
  ink: Ink;
  pacing: { hold: number; transition: number };
  motion_preset?: string; // calm | playful | punchy | cinematic
  labels?: Labels;
};

export type Scene = {
  role: string;
  layout_slug: string;
  text: string;
  display?: string;         // 2-4 word title for typographic layouts
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
  display?: string;         // 2-4 word title; typographic layouts prefer it
  image: string;
  image2?: string;
  recipe: Recipe;
};
