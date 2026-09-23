/**
 * One sky chart, shown wherever it is wanted, without ever being rebuilt.
 *
 * On a wide screen the chart sits in the panel beside the lists. Where the
 * layout stacks into one column -- a phone, a tablet -- it sits in the list
 * itself, directly above the row of the object it is centred on, so the
 * chart, the row and the row's details are one block and nothing has to be
 * scrolled between. Picking another object on the chart moves the block to
 * that object's row.
 *
 * Moving it must not start it again: the zoom, the time stepped to, the
 * orientation, the glide to the new object and the star catalogue already
 * drawn all live inside it. So the chart is rendered once, by the app, into
 * a detached element (a portal), and that one element is moved between
 * slots -- a real DOM move, which React never sees as an unmount. A slot is
 * a `FinderSlot` wherever the chart may appear; the newest inline slot wins,
 * then the panel's, and with neither the chart is simply not on the page.
 *
 * The move happens in the slot's ref callback, during React's commit, so by
 * the time any layout effect runs the chart is already in place and its
 * height counts -- which is what lets the list scroll to it in the same
 * frame.
 */

import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";

type SlotKind = "inline" | "panel";

interface Slots {
  claim: (el: HTMLElement, kind: SlotKind) => void;
  release: (el: HTMLElement) => void;
}

const SlotContext = createContext<Slots | null>(null);
export const FinderSlotProvider = SlotContext.Provider;

/** Where the chart goes when this is the slot that wins. */
export function FinderSlot({ kind }: { kind: SlotKind }) {
  const slots = useContext(SlotContext);
  const mine = useRef<HTMLElement | null>(null);
  const ref = useCallback((el: HTMLDivElement | null) => {
    if (mine.current) slots?.release(mine.current);
    mine.current = el;
    if (el) slots?.claim(el, kind);
  }, [slots, kind]);
  return <div ref={ref} className="finder-slot" />;
}

/** The app's side: the element the chart is portalled into, the context the
 *  slots register with, and whether an inline slot currently holds it. */
export function useFinderHost() {
  const host = useMemo(() => {
    const el = document.createElement("div");
    el.className = "finder-host";
    return el;
  }, []);
  const slots = useRef<{ el: HTMLElement; kind: SlotKind }[]>([]);
  // Focus inside the chart survives a move: a detached element drops it.
  const focusToRestore = useRef<HTMLElement | null>(null);
  const [inline, setInline] = useState(false);

  const value = useMemo<Slots>(() => {
    const place = () => {
      const newest = (kind: SlotKind) =>
        [...slots.current].reverse().find((s) => s.kind === kind)?.el;
      const target = newest("inline") ?? newest("panel") ?? null;
      const active = document.activeElement;
      if (active instanceof HTMLElement && host.contains(active)) focusToRestore.current = active;
      if (target) {
        if (host.parentElement !== target) target.appendChild(host);
        const focus = focusToRestore.current;
        if (focus && host.contains(focus) && document.activeElement !== focus) {
          focus.focus({ preventScroll: true });
        }
        focusToRestore.current = null;
      } else if (host.parentElement) {
        host.remove();
      }
      setInline(slots.current.some((s) => s.kind === "inline"));
    };
    return {
      claim(el, kind) {
        slots.current = [...slots.current.filter((s) => s.el !== el), { el, kind }];
        place();
      },
      release(el) {
        slots.current = slots.current.filter((s) => s.el !== el);
        place();
      },
    };
  }, [host]);

  return { host, slots: value, inline };
}
