/// <reference types="vite/client" />

declare global {
  interface ImportMetaEnv {
    /** The demo banner's "Watch the 3:30 video" link; the link is hidden when empty. */
    readonly VITE_DEMO_VIDEO_URL?: string;
  }
}

export {};
