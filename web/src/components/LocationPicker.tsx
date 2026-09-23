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

import { useEffect, useRef, useState } from "react";
import type { LocationModel } from "../api";

interface Props {
  locations: LocationModel[];
  selected: string;
  onSelect: (key: string) => void;
  onAdd: () => void;
  onEdit: (key: string) => void;
  onDelete: (key: string) => void;
  /** Set while a delete is in flight, to stop a second click. */
  busy?: boolean;
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
  locations, selected, onSelect, onAdd, onEdit, onDelete, busy,
}: Props) {
  const [open, setOpen] = useState(false);
  // Deleting a site is not undoable, so the row asks once. Held here rather
  // than in the parent so closing the popover forgets it.
  const [confirming, setConfirming] = useState<string | null>(null);
  const wrapper = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

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
        <div className="location-menu" role="listbox">
          {locations.map((site) => {
            const isConfirming = confirming === site.key;
            // YAML-defined sites are owned by a file the app must not edit
            // behind the user's back.
            const fromConfig = site.source === "config";
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
                      disabled={busy}
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
                      title={fromConfig
                        ? "Defined in config/locations.yaml"
                        : `Edit ${site.name}`}
                      aria-label={`Edit ${site.name}`}
                      disabled={fromConfig}
                      onClick={() => {
                        onEdit(site.key);
                        setOpen(false);
                      }}
                    >
                      ✎
                    </button>
                    <button
                      className="icon-button"
                      title={fromConfig
                        ? "Defined in config/locations.yaml"
                        : `Remove ${site.name}`}
                      aria-label={`Remove ${site.name}`}
                      disabled={fromConfig || busy}
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
