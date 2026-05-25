/* Searchable dropdown picker — client behavior for searchable_select.html.
 * Coordination is via `closest('.searchable-picker')` + data-attributes, so
 * multiple pickers coexist on one page without per-instance state.
 */
(function () {
    window.LR = window.LR || {};

    function root(el) {
        return el && el.closest ? el.closest('.searchable-picker') : null;
    }

    function applyFilters(picker) {
        var search = picker.querySelector('[data-picker-search]');
        var q = (search ? search.value : '').toLowerCase().trim();
        var activeChip = picker.querySelector('[data-picker-chips] .badge-neutral');
        var typeKey = activeChip ? (activeChip.dataset.filterKey || '') : '';
        picker.querySelectorAll('[data-picker-list] li[data-search]').forEach(function (li) {
            var textMatch = !q || li.dataset.search.toLowerCase().indexOf(q) !== -1;
            var typeMatch = !typeKey || li.dataset.filterKey === typeKey;
            li.classList.toggle('hidden', !(textMatch && typeMatch));
        });
    }

    // Recompute the hidden value + chip strip from the active list items.
    function rebuildMultiState(picker) {
        var hidden = picker.querySelector('[data-picker-value]');
        var chipContainer = picker.querySelector('[data-picker-chip-container]');
        if (!hidden) return;

        var activeButtons = picker.querySelectorAll('[data-picker-list] button.bg-brand-accent\\/15');
        var ids = [];
        var items = [];
        activeButtons.forEach(function (b) {
            ids.push(b.dataset.id);
            items.push({ id: b.dataset.id, label: b.dataset.label });
        });

        hidden.value = ids.join(',');

        if (!chipContainer) return;
        chipContainer.innerHTML = '';
        if (items.length === 0) {
            var placeholder = picker.dataset.placeholder || 'Select...';
            var ph = document.createElement('span');
            ph.className = 'text-base-content/50 text-sm';
            ph.textContent = placeholder;
            chipContainer.appendChild(ph);
            return;
        }
        items.forEach(function (item) {
            var chip = document.createElement('span');
            chip.className = 'badge badge-outline gap-1';
            chip.setAttribute('data-chip-id', item.id);
            var label = document.createElement('span');
            label.className = 'truncate max-w-[10rem]';
            label.textContent = item.label;
            chip.appendChild(label);
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'btn btn-ghost btn-xs btn-circle leading-none';
            btn.setAttribute('data-id', item.id);
            btn.setAttribute('aria-label', 'Remove ' + item.label);
            btn.innerHTML = '&times;';
            // preventDefault on mousedown keeps focus off the trigger, so the
            // DaisyUI dropdown does not re-open when the chip × is clicked.
            btn.onmousedown = function (e) {
                e.stopPropagation();
                e.preventDefault();
            };
            btn.onclick = function (e) {
                e.stopPropagation();
                LR.searchablePicker.removeChip(btn);
            };
            chip.appendChild(btn);
            chipContainer.appendChild(chip);
        });
    }

    // On open, scroll the active item into view. Gated to the trigger
    // (role="button") — running on every focusin would re-scroll during a
    // list-item click and swallow the selection.
    document.addEventListener('focusin', function (e) {
        var picker = root(e.target);
        if (!picker) return;
        if (e.target.getAttribute && e.target.getAttribute('role') !== 'button') return;
        var active = picker.querySelector('[data-picker-list] button.bg-brand-accent\\/15');
        if (active) active.scrollIntoView({ block: 'nearest' });
    });

    LR.searchablePicker = {
        select: function (btn) {
            var picker = root(btn);
            if (!picker) return;
            var hidden = picker.querySelector('[data-picker-value]');
            if (!hidden) return;

            var isMulti = hidden.dataset.multiple === 'true';

            if (isMulti) {
                // Toggle the clicked item and rebuild state. Do NOT blur — the
                // dropdown stays open for further selection.
                var wasActive = btn.classList.contains('bg-brand-accent/15');
                if (wasActive) {
                    btn.classList.remove('bg-brand-accent/15', 'text-brand-accent');
                } else {
                    btn.classList.add('bg-brand-accent/15', 'text-brand-accent');
                }
                rebuildMultiState(picker);
                if (window.htmx) htmx.trigger(hidden, 'change');
                return;
            }

            // Single-mode.
            var label = picker.querySelector('[data-picker-label]');
            if (!label) return;

            hidden.value = btn.dataset.id;
            label.textContent = btn.dataset.label;

            picker.querySelectorAll('[data-picker-list] button').forEach(function (el) {
                el.classList.remove('bg-brand-accent/15', 'text-brand-accent');
            });
            btn.classList.add('bg-brand-accent/15', 'text-brand-accent');

            // DaisyUI dropdown is focus-driven — blurring closes it.
            if (document.activeElement && document.activeElement.blur) {
                document.activeElement.blur();
            }

            if (window.htmx) htmx.trigger(hidden, 'change');
        },

        removeChip: function (chipBtn) {
            var picker = root(chipBtn);
            if (!picker) return;
            var id = chipBtn.dataset.id;
            if (!id) return;
            var listBtn = picker.querySelector('[data-picker-list] button[data-id="' + id + '"]');
            if (listBtn) listBtn.classList.remove('bg-brand-accent/15', 'text-brand-accent');
            rebuildMultiState(picker);
            var hidden = picker.querySelector('[data-picker-value]');
            if (window.htmx && hidden) htmx.trigger(hidden, 'change');
        },

        filter: function (el) {
            var picker = root(el);
            if (picker) applyFilters(picker);
        },

        setFilter: function (chip) {
            var picker = root(chip);
            if (!picker) return;
            picker.querySelectorAll('[data-picker-chips] .badge').forEach(function (c) {
                var on = c === chip;
                c.classList.toggle('badge-neutral', on);
                c.classList.toggle('badge-ghost', !on);
            });
            applyFilters(picker);
        },
    };
})();
