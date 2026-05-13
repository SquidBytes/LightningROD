/* Searchable dropdown picker — shared client behavior.
 *
 * Powers the macro pair in partials/searchable_select.html.  Each picker is a
 * `.searchable-picker` wrapper carrying a hidden form input, a label span,
 * a search input, optional filter chips, and a list of `<button>` items.  All
 * coordination happens via `closest('.searchable-picker')` and data-attributes
 * so there's no per-instance state baked into the markup — multiple pickers
 * coexist on the same page without colliding.
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

    // When a picker opens, scroll its active item into view so the highlight
    // is visible in long lists (e.g. timezone) instead of buried below the fold.
    // Gate to the trigger element (the only descendant with an explicit
    // role="button"). Running on every focusin would re-scroll during a
    // list-item click: focus moves to the clicked <button> on mousedown, the
    // handler jumps the list back to the still-active item, and the mouseup
    // lands on a different row — swallowing the selection.
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
            var label = picker.querySelector('[data-picker-label]');
            if (!hidden || !label) return;

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
