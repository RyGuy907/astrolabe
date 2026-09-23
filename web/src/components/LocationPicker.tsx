/**
 * The site selector, with add / edit / delete on the rows themselves.
 *
 * A native <select> cannot hold a button, so choosing a site and managing
 * sites had to be two separate controls -- a picker and a "Sites…" button
 * that opened a modal containing a second copy of the list. One list is
 * enough: this is a popover, the rows carry their own edit and delete
 * controls, and adding a site is the last row rather than a separate button
 * in the toolbar.
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { LocationModel } from "../api";

interface Props {
  locations: LocationModel[];
  selected: string;
  onSelect: (key: string) => void;
  onAdd: () => void;
  onEdit: (key: string) => void;
  onDelete: (key: string) => void;
}

/** How a site's sky reads on a row: three different degrees of knowing. */
function skyLabel(site: LocationModel): string {
  switch (site.sky_source) {
    case "observer":
      return `Bortle ${site.bortle}`;
    case "atlas":
      return `Bortle ${(site.bortle_decimal ?? site.effective_bortle).toFixed(1)} · atlas`;
    default:
      return "Bortle 5 assumed";
  }
}

export function LocationPicker({
  locations, selected, onSelect, onAdd, onEdit, onDelete,
}: Props) {
  const [open, setOpen] = useState(false);
  // Deleting a site is not undoable, so the row asks once. Held here rather
  // than in the parent so closing the popover forgets it. Every site is this
  // browser's own, so every one can be edited and deleted.
  const [confirming, setConfirming] = useState<string | null>(null);
  const wrapper = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);

  // Opened under the button, starting at its left edge -- and slid left as
  // far as it must to stay on the screen. Where the button sits moves with
  // the layout (the right of the header on a tablet, the left on a phone),
  // and a menu anchored to one side for one layout ran off the page in the
  // other.
  useLayoutEffect(() => {
    const list = menu.current;
    const button = wrapper.current;
    if (!open || !list || !button) return;
    const place = () => {
      const box = button.getBoundingClientRect();
      const gutter = 12;
      const room = document.documentElement.clientWidth - gutter;
      const overhang = box.left + list.offsetWidth - room;
      list.style.left = `${overhang > 0 ? Math.max(gutter - box.left, -overhang) : 0}px`;
    };
    place();
    // Again if the header reflows while it is open -- the date arriving
    // beside the button on a first load, or the window turning.
    const observer = new ResizeObserver(place);
    observer.observe(button);
    window.addEventListener("resize", place);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", place);
    };
  }, [open]);

  const current = locations.find((l) => l.key === selected);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (!wrapper.current?.contains(event.target as Node)) close();
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") close();
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function close() {
    setOpen(false);
    setConfirming(null);
    // Focus goes back where it came from, or it lands on <body> and the next
    // Tab starts from the top of the page.
    trigger.current?.focus();
  }

  return (
    <div className="location-picker" ref={wrapper}>
      <button
        ref={trigger}
        className="location-trigger"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-haspopup="listbox"
      >
        <span className="location-trigger-name">
          {current ? current.name : "Choose a site"}
        </span>
        <span className="caret">▾</span>
      </button>

      {open && (
        <div className="location-menu" role="listbox" ref={menu}>
          {locations.map((site) => {
            const isConfirming = confirming === site.key;
            return (
              <div
                key={site.key}
                className={`location-row ${site.key === selected ? "on" : ""}`}
              >
                <button
                  className="location-choose"
                  role="option"
                  aria-selected={site.key === selected}
                  onClick={() => {
                    onSelect(site.key);
                    close();
                  }}
                >
                  <span className="location-row-name">{site.name}</span>
                  <span className="muted small">
                    {site.lat.toFixed(2)}, {site.lon.toFixed(2)} ·{" "}
                    {skyLabel(site)}
                  </span>
                </button>

                {isConfirming ? (
                  <span className="location-confirm">
                    <button
                      className="danger"
                      onClick={() => {
                        onDelete(site.key);
                        setConfirming(null);
                      }}
                    >
                      Delete
                    </button>
                    <button
                      className="secondary"
                      onClick={() => setConfirming(null)}
                    >
                      Cancel
                    </button>
                  </span>
                ) : (
                  <span className="location-row-actions">
                    <button
                      className="icon-button"
                      title={`Edit ${site.name}`}
                      aria-label={`Edit ${site.name}`}
                      onClick={() => {
                        onEdit(site.key);
                        setOpen(false);
                      }}
                    >
                      ✎
                    </button>
                    <button
                      className="icon-button"
                      title={`Remove ${site.name}`}
                      aria-label={`Remove ${site.name}`}
                      onClick={() => setConfirming(site.key)}
                    >
                      ✕
                    </button>
                  </span>
                )}
              </div>
            );
          })}

          <button
            className="location-add"
            onClick={() => {
              onAdd();
              setOpen(false);
            }}
          >
            + Add a site
          </button>
        </div>
      )}
    </div>
  );
}
