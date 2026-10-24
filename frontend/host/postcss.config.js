export default {
  plugins: {
    // Name the config explicitly: a stale generated tailwind.config.js would otherwise win.
    tailwindcss: { config: './tailwind.config.ts' },
    autoprefixer: {},
  },
};
