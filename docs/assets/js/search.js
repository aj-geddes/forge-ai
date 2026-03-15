/**
 * Search — Client-side search through page titles and content.
 * Ctrl/Cmd+K to open, fuzzy matching, keyboard navigation.
 */
(function () {
  'use strict';

  var searchInput   = document.getElementById('search-input');
  var searchResults = document.getElementById('search-results');
  if (!searchInput || !searchResults) return;

  var searchIndex = [];
  var focusedIndex = -1;
  var isOpen = false;

  // -----------------------------------------------------------------------
  // Build search index from page data
  // -----------------------------------------------------------------------
  function buildIndex() {
    // Gather all sidebar links as the primary search source
    var links = document.querySelectorAll('.sidebar-link');
    links.forEach(function (link) {
      var section = link.closest('.sidebar-section');
      var sectionTitle = '';
      if (section) {
        var header = section.querySelector('.sidebar-section-title');
        if (header) sectionTitle = header.textContent.trim();
      }

      searchIndex.push({
        title: link.textContent.trim(),
        url: link.getAttribute('href'),
        section: sectionTitle
      });
    });
  }

  buildIndex();

  // -----------------------------------------------------------------------
  // Fuzzy match
  // -----------------------------------------------------------------------
  function matchScore(query, text) {
    var lower = text.toLowerCase();
    var q = query.toLowerCase();

    // Exact substring match gets highest score
    if (lower.indexOf(q) !== -1) {
      return 100 - lower.indexOf(q);
    }

    // Word-start matching
    var words = lower.split(/\s+/);
    var wordMatch = words.some(function (w) {
      return w.indexOf(q) === 0;
    });
    if (wordMatch) return 50;

    // Character sequence matching (fuzzy)
    var qi = 0;
    var consecutive = 0;
    var maxConsecutive = 0;
    for (var i = 0; i < lower.length && qi < q.length; i++) {
      if (lower[i] === q[qi]) {
        qi++;
        consecutive++;
        if (consecutive > maxConsecutive) maxConsecutive = consecutive;
      } else {
        consecutive = 0;
      }
    }

    if (qi === q.length) {
      return maxConsecutive * 5;
    }

    return 0;
  }

  // -----------------------------------------------------------------------
  // Search and render results
  // -----------------------------------------------------------------------
  function performSearch(query) {
    if (!query || query.length < 2) {
      closeResults();
      return;
    }

    var results = [];
    searchIndex.forEach(function (item) {
      var titleScore = matchScore(query, item.title);
      var sectionScore = matchScore(query, item.section) * 0.5;
      var score = Math.max(titleScore, sectionScore);
      if (score > 0) {
        results.push({ item: item, score: score });
      }
    });

    results.sort(function (a, b) { return b.score - a.score; });
    results = results.slice(0, 8);

    renderResults(results);
  }

  function renderResults(results) {
    searchResults.innerHTML = '';
    focusedIndex = -1;

    if (results.length === 0) {
      searchResults.innerHTML = '<div class="search-no-results">No results found</div>';
      openResults();
      return;
    }

    results.forEach(function (result, index) {
      var link = document.createElement('a');
      link.href = result.item.url;
      link.className = 'search-result-item';
      link.setAttribute('role', 'option');
      link.setAttribute('data-index', index);
      link.innerHTML =
        '<div class="search-result-title">' + escapeHtml(result.item.title) + '</div>' +
        '<div class="search-result-section">' + escapeHtml(result.item.section) + '</div>';
      searchResults.appendChild(link);
    });

    openResults();
  }

  function escapeHtml(text) {
    var div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  // -----------------------------------------------------------------------
  // Open/close results dropdown
  // -----------------------------------------------------------------------
  function openResults() {
    searchResults.classList.add('active');
    isOpen = true;
  }

  function closeResults() {
    searchResults.classList.remove('active');
    searchResults.innerHTML = '';
    isOpen = false;
    focusedIndex = -1;
  }

  // -----------------------------------------------------------------------
  // Keyboard navigation
  // -----------------------------------------------------------------------
  function updateFocus() {
    var items = searchResults.querySelectorAll('.search-result-item');
    items.forEach(function (item, i) {
      if (i === focusedIndex) {
        item.classList.add('focused');
        item.scrollIntoView({ block: 'nearest' });
      } else {
        item.classList.remove('focused');
      }
    });
  }

  searchInput.addEventListener('input', function () {
    performSearch(this.value.trim());
  });

  searchInput.addEventListener('keydown', function (e) {
    var items = searchResults.querySelectorAll('.search-result-item');

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        if (isOpen && items.length > 0) {
          focusedIndex = Math.min(focusedIndex + 1, items.length - 1);
          updateFocus();
        }
        break;

      case 'ArrowUp':
        e.preventDefault();
        if (isOpen && items.length > 0) {
          focusedIndex = Math.max(focusedIndex - 1, 0);
          updateFocus();
        }
        break;

      case 'Enter':
        e.preventDefault();
        if (focusedIndex >= 0 && items[focusedIndex]) {
          window.location.href = items[focusedIndex].href;
        }
        break;

      case 'Escape':
        closeResults();
        searchInput.blur();
        break;
    }
  });

  // Close on outside click
  document.addEventListener('click', function (e) {
    if (!searchInput.contains(e.target) && !searchResults.contains(e.target)) {
      closeResults();
    }
  });

  // -----------------------------------------------------------------------
  // Ctrl/Cmd+K shortcut
  // -----------------------------------------------------------------------
  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
      e.preventDefault();
      searchInput.focus();
      searchInput.select();
    }
  });
})();
