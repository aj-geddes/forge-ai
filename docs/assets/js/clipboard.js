/**
 * Clipboard — Adds copy buttons to all code blocks and handles
 * the copy-to-clipboard action with visual feedback.
 */
(function () {
  'use strict';

  var COPIED_DURATION = 2000;

  // SVG icons
  var copyIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
  var checkIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>';

  function addCopyButtons() {
    var codeBlocks = document.querySelectorAll('div.highlight, .highlighter-rouge');

    codeBlocks.forEach(function (block) {
      // Skip if already processed
      if (block.querySelector('.code-copy-btn')) return;

      // Ensure relative positioning for the button
      var computedPos = window.getComputedStyle(block).position;
      if (computedPos === 'static') {
        block.style.position = 'relative';
      }

      var button = document.createElement('button');
      button.className = 'code-copy-btn';
      button.setAttribute('aria-label', 'Copy code');
      button.setAttribute('title', 'Copy');
      button.innerHTML = copyIcon + '<span>Copy</span>';

      button.addEventListener('click', function () {
        var code = block.querySelector('code');
        if (!code) return;

        var text = code.textContent || '';

        // Use the Clipboard API if available, fallback to execCommand
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(function () {
            showCopied(button);
          }).catch(function () {
            fallbackCopy(text, button);
          });
        } else {
          fallbackCopy(text, button);
        }
      });

      block.appendChild(button);
    });
  }

  function fallbackCopy(text, button) {
    var textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    textarea.style.left = '-9999px';
    document.body.appendChild(textarea);
    textarea.select();

    try {
      document.execCommand('copy');
      showCopied(button);
    } catch (e) {
      // Copy failed silently
    }

    document.body.removeChild(textarea);
  }

  function showCopied(button) {
    button.innerHTML = checkIcon + '<span>Copied!</span>';
    button.classList.add('copied');

    setTimeout(function () {
      button.innerHTML = copyIcon + '<span>Copy</span>';
      button.classList.remove('copied');
    }, COPIED_DURATION);
  }

  // Initialize on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', addCopyButtons);
  } else {
    addCopyButtons();
  }
})();
