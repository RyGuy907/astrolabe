/**
 * The draggable divider between the altitude chart and the Deep Sky /
 * Solar System / Events panel.
 *
 * The split is a fraction of the row -- the chart's share -- held by the
 * parent, so the loading skeleton can lay itself out the same way and a
 * reload keeps it. Clamped to 25-75%: past that one side is too narrow to
 * read, and a divider dragged to the edge is hard to find again.
 *
 * A separator in ARIA terms, so it is focusable and announced with its
 * position; the arrow keys move it in 2% steps, Home and End go to the
 * limits, and a double-click puts it back where it started.
 */

import { useRef } from "react";

export const SPLIT_MIN = 0.25;
export const SPLIT_MAX = 0.75;
/** The chart's share before anyone drags: the old fixed 1 : 1.15 columns. */
export const SPLIT_DEFAULT = 1 / 2.15;

/** Width of the handle's track and the column gap either side, in px. These
 *  must match `.dashboard-row.resizable` in the stylesheet. */
const HANDLE_PX = 12;
const GAP_PX = 16;

const clamp = (value: number) => Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, value));

interface Props {
  /** The row being divided, to measure against while dragging. */
  rowRef: React.RefObject<HTMLDivElement>;
  split: number;
  onChange: (split: number) => void;
}

export function Splitter({ rowRef, split, onChange }: Props) {
  const dragging = useRef(false);

  /** The split that puts the handle's centre under the pointer. */
  function splitAt(clientX: number): number | null {
    const row = rowRef.current;
    if (!row) return null;
    const rect = row.getBoundingClientRect();
    const available = rect.width - HANDLE_PX - 2 * GAP_PX;
    if (available <= 0) return null;
    const firstWidth = clientX - rect.left - GAP_PX - HANDLE_PX / 2;
    return clamp(firstWidth / available);
  }

  function onPointerDown(event: React.PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    event.preventDefault();
    // Capture, so the drag keeps tracking when the pointer runs ahead of the
    // handle and over the chart or a table.
    // Best effort: the browser refuses capture for a pointer it does not
    // consider active, and losing capture should not lose the drag.
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      /* the drag still works while the pointer stays over the handle */
    }
    dragging.current = true;
    document.body.classList.add("is-resizing");
  }

  function onPointerMove(event: React.PointerEvent<HTMLDivElement>) {
    if (!dragging.current) return;
    const next = splitAt(event.clientX);
    if (next !== null) onChange(next);
  }

  function stop(event: React.PointerEvent<HTMLDivElement>) {
    if (!dragging.current) return;
    dragging.current = false;
    document.body.classList.remove("is-resizing");
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    const steps: Record<string, number> = {
      ArrowLeft: split - 0.02,
      ArrowRight: split + 0.02,
      Home: SPLIT_MIN,
      End: SPLIT_MAX,
    };
    if (!(event.key in steps)) return;
    event.preventDefault();
    onChange(clamp(steps[event.key]));
  }

  return (
    <div
      className="splitter"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize the chart and the lists"
      aria-valuemin={SPLIT_MIN * 100}
      aria-valuemax={SPLIT_MAX * 100}
      aria-valuenow={Math.round(split * 100)}
      tabIndex={0}
      title="Drag to resize · double-click to reset"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={stop}
      onPointerCancel={stop}
      onDoubleClick={() => onChange(SPLIT_DEFAULT)}
      onKeyDown={onKeyDown}
    >
      <span className="splitter-grip" aria-hidden="true" />
    </div>
  );
}
