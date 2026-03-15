/**
 * Navigation — Sidebar toggle, active page highlighting, section
 * collapse/expand, dark mode toggle with localStorage persistence.
 */
(function () {
  'use strict';

  // -----------------------------------------------------------------------
  // DOM references
  // -----------------------------------------------------------------------
  var sidebar       = document.getElementById('sidebar');
  var overlay       = document.getElementById('sidebar-overlay');
  var menuBtn       = document.querySelector('.mobile-menu-btn');
  var themeToggle   = document.getElementById('theme-toggle');
  var sectionHeaders = document.querySelectorAll('.sidebar-section-header');

  // -----------------------------------------------------------------------
  // Mobile sidebar toggle
  // -----------------------------------------------------------------------
  function openSidebar() {
    if (!sidebar || !overlay || !menuBtn) return;
    sidebar.classList.add('open');
    overlay.classList.add('active');
    menuBtn.setAttribute('aria-expanded', 'true');
    document.body.style.overflow = 'hidden';
  }

  function closeSidebar() {
    if (!sidebar || !overlay || !menuBtn) return;
    sidebar.classList.remove('open');
    overlay.classList.remove('active');
    menuBtn.setAttribute('aria-expanded', 'false');
    document.body.style.overflow = '';
  }

  if (menuBtn) {
    menuBtn.addEventListener('click', function () {
      var expanded = menuBtn.getAttribute('aria-expanded') === 'true';
      if (expanded) {
        closeSidebar();
      } else {
        openSidebar();
      }
    });
  }

  if (overlay) {
    overlay.addEventListener('click', closeSidebar);
  }

  // Close sidebar on escape key
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && sidebar && sidebar.classList.contains('open')) {
      closeSidebar();
    }
  });

  // -----------------------------------------------------------------------
  // Sidebar section collapse/expand
  // -----------------------------------------------------------------------
  sectionHeaders.forEach(function (header) {
    header.addEventListener('click', function () {
      var expanded = header.getAttribute('aria-expanded') === 'true';
      header.setAttribute('aria-expanded', expanded ? 'false' : 'true');

      var linksId = header.getAttribute('aria-controls');
      var links = document.getElementById(linksId);
      if (links) {
        if (expanded) {
          links.style.maxHeight = '0';
          links.style.opacity = '0';
          links.style.padding = '0';
        } else {
          links.style.maxHeight = links.scrollHeight + 'px';
          links.style.opacity = '1';
          links.style.padding = '';
        }
      }
    });
  });

  // -----------------------------------------------------------------------
  // Active page highlighting
  // -----------------------------------------------------------------------
  var currentPath = window.location.pathname;
  var sidebarLinks = document.querySelectorAll('.sidebar-link');

  sidebarLinks.forEach(function (link) {
    var href = link.getAttribute('href');
    if (href && currentPath === href) {
      link.classList.add('active');

      // Ensure parent section is expanded
      var section = link.closest('.sidebar-section');
      if (section) {
        var sectionHeader = section.querySelector('.sidebar-section-header');
        if (sectionHeader) {
          sectionHeader.setAttribute('aria-expanded', 'true');
          var linksContainer = section.querySelector('.sidebar-links');
          if (linksContainer) {
            linksContainer.style.maxHeight = '';
            linksContainer.style.opacity = '1';
          }
        }
      }

      // Scroll active link into view within sidebar
      if (sidebar) {
        var linkRect = link.getBoundingClientRect();
        var sidebarRect = sidebar.getBoundingClientRect();
        if (linkRect.top < sidebarRect.top || linkRect.bottom > sidebarRect.bottom) {
          link.scrollIntoView({ block: 'center', behavior: 'smooth' });
        }
      }
    }
  });

  // -----------------------------------------------------------------------
  // Dark mode toggle with localStorage persistence
  // -----------------------------------------------------------------------
  var THEME_KEY = 'forge-docs-theme';

  function getPreferredTheme() {
    var stored = localStorage.getItem(THEME_KEY);
    if (stored) return stored;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem(THEME_KEY, theme);
  }

  // Apply on load (also handled by inline script in head for flash prevention)
  applyTheme(getPreferredTheme());

  if (themeToggle) {
    themeToggle.addEventListener('click', function () {
      var current = document.documentElement.getAttribute('data-theme') || 'light';
      var next = current === 'dark' ? 'light' : 'dark';
      applyTheme(next);
    });
  }

  // Listen for system theme changes
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function (e) {
    if (!localStorage.getItem(THEME_KEY)) {
      applyTheme(e.matches ? 'dark' : 'light');
    }
  });

  // -----------------------------------------------------------------------
  // Close sidebar on window resize past breakpoint
  // -----------------------------------------------------------------------
  var lgBreakpoint = 992;
  window.addEventListener('resize', function () {
    if (window.innerWidth > lgBreakpoint && sidebar && sidebar.classList.contains('open')) {
      closeSidebar();
    }
  });
})();
