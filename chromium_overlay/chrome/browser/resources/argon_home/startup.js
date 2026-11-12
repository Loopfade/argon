// Run before CSS/body parsing, including when the application module fails.
(() => {
  'use strict';
  const root = document.documentElement;
  root.classList.add('argon-home-starting');
  let revealed = false;
  let deadline;
  function finish(painted = () => {}) {
    if (!revealed) {
      revealed = true;
      clearTimeout(deadline);
      root.classList.remove('argon-home-starting');
    }
    // A late native connection after the failure deadline still needs a new
    // frame with its final provider/layout before notifying the Android cover.
    requestAnimationFrame(() => requestAnimationFrame(painted));
  }
  globalThis.argonHomeStartup = Object.freeze({finish});
  // Failure deadline only. Normal startup finishes as soon as settings arrive.
  deadline = setTimeout(() => finish(), 1500);
})();
