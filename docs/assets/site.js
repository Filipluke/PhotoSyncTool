'use strict';
const views = {
  gallery: ['Gallery with demo photos, filters and a detailed preview', 'Browse locally indexed photos and inspect a larger preview. Screenshots use a disposable demo library.'],
  dashboard: ['Dashboard with library totals and recent sync activity', 'See library totals, review queues, and recent import activity in one place. Demonstration data.'],
  duplicates: ['Duplicate review with candidate files and a comparison preview', 'Compare duplicate candidates before adding them to the cleanup queue. Demonstration data.']
};
for (const button of document.querySelectorAll('[data-view]')) {
  button.addEventListener('click', () => {
    const view = button.dataset.view;
    if (!Object.hasOwn(views, view)) return;
    const screenshot = document.getElementById('app-screenshot');
    screenshot.src = `screenshots/${view}.png`;
    screenshot.alt = views[view][0];
    document.getElementById('view-caption').textContent = views[view][1];
    for (const item of document.querySelectorAll('[data-view]')) item.setAttribute('aria-pressed', String(item === button));
  });
}
document.getElementById('copy-install').addEventListener('click', async () => {
  const code = document.getElementById('install-command');
  const status = document.getElementById('copy-status');
  try {
    await navigator.clipboard.writeText(code.textContent);
    status.textContent = 'Copied!';
  } catch {
    const range = document.createRange();
    range.selectNodeContents(code);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    status.textContent = 'Selected — press Ctrl+C / ⌘C.';
  }
});
