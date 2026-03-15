/**
 * Table of Contents — Generates TOC from page headings and provides
 * scroll-spy highlighting for the current visible section.
 */
(function () {
  'use strict';

  var tocNav = document.getElementById('toc-nav');
  if (!tocNav) return;

  var HEADER_OFFSET = 80; // header height + some padding
  var headings = [];
  var tocLinks = [];

  // -----------------------------------------------------------------------
  // Build TOC from page headings
  // -----------------------------------------------------------------------
  function buildToc() {
    var prose = document.querySelector('.prose');
    if (!prose) return;

    var elements = prose.querySelectorAll('h2, h3, h4');
    if (elements.length === 0) {
      // Hide TOC if there are no headings
      var tocSidebar = document.getElementById('toc-sidebar');
      if (tocSidebar) tocSidebar.style.display = 'none';
      return;
    }

    var fragment = document.createDocumentFragment();

    elements.forEach(function (heading) {
      // Ensure heading has an id for linking
      if (!heading.id) {
        heading.id = generateId(heading.textContent);
      }

      // Add anchor link to heading
      var anchor = document.createElement('a');
      anchor.href = '#' + heading.id;
      anchor.className = 'heading-anchor';
      anchor.setAttribute('aria-hidden', 'true');
      anchor.textContent = '#';
      heading.insertBefore(anchor, heading.firstChild);

      // Create TOC link
      var link = document.createElement('a');
      link.href = '#' + heading.id;
      link.textContent = heading.textContent.replace(/^#\s*/, '');
      link.className = 'toc-' + heading.tagName.toLowerCase();

      link.addEventListener('click', function (e) {
        e.preventDefault();
        var target = document.getElementById(heading.id);
        if (target) {
          var top = target.getBoundingClientRect().top + window.pageYOffset - HEADER_OFFSET;
          window.scrollTo({ top: top, behavior: 'smooth' });
          history.replaceState(null, '', '#' + heading.id);
        }
      });

      fragment.appendChild(link);

      headings.push(heading);
      tocLinks.push(link);
    });

    tocNav.appendChild(fragment);
  }

  function generateId(text) {
    return text
      .toLowerCase()
      .replace(/[^a-z0-9\s-]/g, '')
      .replace(/\s+/g, '-')
      .replace(/-+/g, '-')
      .replace(/^-|-$/g, '');
  }

  // -----------------------------------------------------------------------
  // Scroll spy
  // -----------------------------------------------------------------------
  var ticking = false;

  function onScroll() {
    if (ticking) return;
    ticking = true;

    requestAnimationFrame(function () {
      updateActiveHeading();
      ticking = false;
    });
  }

  function updateActiveHeading() {
    if (headings.length === 0) return;

    var scrollTop = window.pageYOffset || document.documentElement.scrollTop;
    var activeIndex = -1;

    // Find the heading closest to the top of the viewport
    for (var i = headings.length - 1; i >= 0; i--) {
      var headingTop = headings[i].getBoundingClientRect().top + scrollTop;
      if (scrollTop >= headingTop - HEADER_OFFSET - 20) {
        activeIndex = i;
        break;
      }
    }

    // If near top of page, activate first heading
    if (activeIndex === -1 && scrollTop < 100) {
      activeIndex = 0;
    }

    tocLinks.forEach(function (link, i) {
      if (i === activeIndex) {
        link.classList.add('toc-active');
      } else {
        link.classList.remove('toc-active');
      }
    });
  }

  // -----------------------------------------------------------------------
  // Initialize
  // -----------------------------------------------------------------------
  buildToc();

  if (headings.length > 0) {
    window.addEventListener('scroll', onScroll, { passive: true });
    // Initial highlight
    updateActiveHeading();
  }

  // Handle hash on page load
  if (window.location.hash) {
    var target = document.querySelector(window.location.hash);
    if (target) {
      setTimeout(function () {
        var top = target.getBoundingClientRect().top + window.pageYOffset - HEADER_OFFSET;
        window.scrollTo({ top: top, behavior: 'smooth' });
      }, 100);
    }
  }
})();
