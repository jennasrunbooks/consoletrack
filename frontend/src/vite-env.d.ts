/// <reference types="vite/client" />

declare global {
  interface ImportMetaEnv {
    /** The demo banner's "Watch the 2-min video" link; the link is hidden when empty. */
    readonly VITE_DEMO_VIDEO_URL?: string;
  }
}

export {};
