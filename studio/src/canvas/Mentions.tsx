import { useLayoutEffect, useRef, useState } from "react";
import type { PictureRef } from "../beat";
import { mentionsIn } from "../beat";

/**
 * Typing `@` in a prompt field to name one of this scene's pictures.
 *
 * The token a field stores carries the picture's **id**, never its number, and that is the whole
 * design. The same text is read by two prompt builders with two incompatible orderings — the
 * video model gets `<Picture N>` off one list, the still model gets prose off another,
 * identity sheets first and capped — so no single literal is correct in both places. And
 * `ref_offset` moves whenever a still lands, a cast reference is pinned, carry is ticked or
 * the join is cycled: four events that touch no text and would silently relabel a number typed
 * into prose. The server expands tokens at render time; this only inserts them and shows what
 * they currently mean.
 *
 * Deliberately narrow. The menu is an absolutely positioned div inside a wrapper the field owns:
 * no portal, no fixed positioning, no collision detection, and no generic Popover in `ui.tsx`.
 * It anchors to the FIELD rather than the caret, because caret anchoring needs a mirror div to
 * measure a text offset inside a textarea, and this reads fine in a 26rem column.
 */
export function useMentions({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (next: string) => void;
  options: PictureRef[];
}) {
  const field = useRef<HTMLTextAreaElement | null>(null);
  const menu = useRef<HTMLDivElement | null>(null);
  const [query, setQuery] = useState<{ at: number; text: string } | null>(null);
  const [active, setActive] = useState(0);
  // Bumped on every insert so the layout effect below knows to put the caret back.
  const [inserted, setInserted] = useState<number | null>(null);

  const usable = options.filter((option) => option.token);
  const matches = query
    ? usable.filter((option) =>
        !query.text || option.label.toLowerCase().includes(query.text.toLowerCase())
        || option.note.toLowerCase().includes(query.text.toLowerCase()))
    : [];
  const open = query !== null && matches.length > 0;

  useLayoutEffect(() => {
    if (inserted === null) return;
    // React does not preserve selection across a programmatic value change on a controlled
    // textarea — without this the caret jumps to the end and typing continues in the wrong place.
    field.current?.setSelectionRange(inserted, inserted);
    field.current?.focus();
    setInserted(null);
  }, [inserted]);

  useLayoutEffect(() => {
    // The modal's right column is itself `overflow-y-auto`, so an absolutely positioned menu on
    // a field near its bottom is clipped by the scroller. One line, rather than a measuring pass.
    if (open) menu.current?.scrollIntoView({ block: "nearest" });
  }, [open]);

  /** Where an unfinished `@…` starts, if the caret is inside one. */
  const findQuery = (text: string, caret: number) => {
    const before = text.slice(0, caret);
    const at = before.lastIndexOf("@");
    if (at < 0) return null;
    // Only at a word boundary, so an email address or a stored `@ref:abc123` is not a trigger.
    if (at > 0 && !/[\s(\[]/.test(before[at - 1])) return null;
    const typed = before.slice(at + 1);
    if (!/^[\w:]*$/.test(typed) || typed.includes(":")) return null;
    return { at, text: typed };
  };

  const accept = (option: PictureRef) => {
    if (!query || !option.token) return;
    const caret = query.at + query.text.length + 1;
    const next = `${value.slice(0, query.at)}${option.token} ${value.slice(caret)}`;
    setQuery(null);
    setActive(0);
    // Through the field's own commit, never a parallel setState — otherwise `useDraft`'s
    // debounce does not restart and the next board event yanks the insert straight back out.
    onChange(next);
    setInserted(query.at + option.token.length + 1);
  };

  /** True when the menu consumed the key. Every call site returns early on true. */
  const onKeyDown = (event: React.KeyboardEvent): boolean => {
    if (!open) return false;
    const owned = ["ArrowDown", "ArrowUp", "Enter", "Tab", "Escape"];
    if (!owned.includes(event.key)) return false;
    event.preventDefault();
    // Stopped as well as prevented, because `BeatModal` binds Escape on `window` and React
    // attaches its own listeners at the root below it — so this is what makes the first Escape
    // close the menu and the second close the modal, rather than one Escape doing both.
    event.stopPropagation();
    if (event.key === "Escape") {
      setQuery(null);
      return true;
    }
    if (event.key === "ArrowDown") {
      setActive((current) => (current + 1) % matches.length);
      return true;
    }
    if (event.key === "ArrowUp") {
      setActive((current) => (current - 1 + matches.length) % matches.length);
      return true;
    }
    accept(matches[active] ?? matches[0]);
    return true;
  };

  const onInput = (event: React.ChangeEvent<HTMLTextAreaElement>) => {
    const next = findQuery(event.target.value, event.target.selectionStart ?? 0);
    setQuery(next);
    setActive(0);
    onChange(event.target.value);
  };

  const dropdown = open ? (
    <div
      ref={menu}
      // Without this, clicking a row blurs the textarea first, `onBlur={flush}` fires, and the
      // menu unmounts before the click ever lands.
      onMouseDown={(event) => event.preventDefault()}
      className="lift-lg absolute left-0 right-0 top-full z-10 mt-1 max-h-48 overflow-y-auto
        rounded-xl border border-edge bg-panel p-1"
    >
      {matches.map((option, at) => (
        <button
          key={option.token}
          disabled={Boolean(option.unavailable)}
          onClick={() => accept(option)}
          className={`flex w-full items-center gap-2 rounded-lg px-1.5 py-1 text-left ${
            option.unavailable
              ? "cursor-not-allowed opacity-40"
              : at === active
                ? "bg-soft"
                : "hover:bg-soft"
          }`}
        >
          <span className="flex h-6 w-6 shrink-0 items-center justify-center overflow-hidden
            rounded-md bg-ink">
            {option.url ? (
              <img src={option.url} alt="" className="h-full w-full object-cover" />
            ) : null}
          </span>
          <span className="truncate text-[10px] text-zinc-700">{option.label}</span>
          {/* The number THIS field uses, which is the whole point: the same picture reads as
              <Picture 4> in the action and as an ordinal in the draw prompt. */}
          <span className="ml-auto shrink-0 text-[10px] text-zinc-500">
            {option.unavailable ?? option.tag}
          </span>
        </button>
      ))}
    </div>
  ) : null;

  return { field, dropdown, onKeyDown, onInput, close: () => setQuery(null) };
}

/**
 * A textarea that understands `@`, plus the legend that says what its tokens currently mean.
 *
 * The legend is under the field rather than inline, and that is the better design here rather
 * than only the cheaper one. An inline chip can show one string; the entire reason tokens exist
 * is that the same picture has two names depending on which field you are in. The legend shows
 * the token and its expansion for THIS field, side by side — and it surfaces a dangling token,
 * which an inline chip cannot.
 */
export function PromptField({
  value,
  onChange,
  onBlur,
  options,
  onPick,
  className,
  placeholder,
  title,
}: {
  value: string;
  onChange: (next: string) => void;
  onBlur?: () => void;
  options: PictureRef[];
  /** Clicking a legend chip selects that picture, so the legend is navigation not decoration. */
  onPick?: (option: PictureRef) => void;
  className?: string;
  placeholder?: string;
  title?: string;
}) {
  const mentions = useMentions({ value, onChange, options });
  const present = mentionsIn(value);

  return (
    <div className="relative space-y-1">
      <textarea
        ref={mentions.field}
        className={className}
        value={value}
        onChange={mentions.onInput}
        onBlur={() => {
          mentions.close();
          onBlur?.();
        }}
        onKeyDown={(event) => {
          if (mentions.onKeyDown(event)) return;
        }}
        placeholder={placeholder}
        title={title}
      />
      {mentions.dropdown}
      {present.length ? (
        <div className="flex flex-wrap gap-1">
          {present.map((id, at) => {
            const found = options.find((option) => option.id === id);
            return (
              <button
                key={`${id}-${at}`}
                onClick={() => found && onPick?.(found)}
                title={
                  found
                    ? `${found.label} — this field calls it ${found.unavailable ?? found.tag}`
                    : "this picture is no longer on the scene, so the render drops the token"
                }
                className={`rounded bg-soft px-1 py-0.5 text-[9px] ${
                  found ? "text-zinc-600 hover:text-warm" : "text-stale"
                }`}
              >
                {found ? `${found.label} → ${found.unavailable ?? found.tag}` : "unknown picture"}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
