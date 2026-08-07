import { useRef, useState } from "react";
import { api } from "../api";
import type { Board } from "../types";
import { useStudio } from "../useStudio";
import { Button } from "../ui";

/**
 * Filling in a whole reel's opening stills at once, and the switch that turns generation off.
 *
 * An imported script arrives with its shots already decided, so what is left is one image per
 * cut -- and those often already exist, made somewhere else entirely. Doing that a node at a
 * time is six pickers and six drops; doing it here is one selection.
 *
 * Uploading never spends anything, and a board set to "my own" cannot generate at all. What
 * *generating* costs depends on which backend is up: the local mflux renderer is free and
 * unlimited, agy is capped at roughly five images per five hours. The copy below follows
 * `studio.stillsBackend` rather than stating one of them as fact.
 */
export function FillStills() {
  const studio = useStudio();
  const board = studio.board!;
  const picker = useRef<HTMLInputElement>(null);
  const [dropping, setDropping] = useState(false);
  const [busy, setBusy] = useState<{ done: number; total: number } | null>(null);
  const [placed, setPlaced] = useState<string[]>([]);

  const needed = board.assets_needed;
  const local = studio.stillsBackend === "papercut";

  const fill = async (chosen: FileList | null) => {
    setDropping(false);
    const files = Array.from(chosen ?? []).filter((file) => file.type.startsWith("image/"));
    if (!files.length) return;

    const { targets, unused } = assign(files, board);
    if (!targets.length) {
      studio.setError(
        needed.length === 0
          ? "every beat already has its still — name a file beat2.png to replace one in particular"
          : "could not tell which beat those files are for",
      );
      return;
    }

    setPlaced([]);
    setBusy({ done: 0, total: targets.length });
    const landed: string[] = [];
    const failed: string[] = [];
    // Sequential rather than parallel: each upload changes its beat's join and republishes the
    // board, and the canvas is more legible filling in one node at a time than all at once.
    for (const [index, target] of targets.entries()) {
      try {
        // A bridge keeps its join -- its still is the frame it lands on, and a bulk fill must
        // not quietly turn a continuation the user chose into a cut. Every other beat treats a
        // supplied still as its opening frame.
        const join =
          board.beats.find((beat) => beat.n === target.beat)?.source === "bridge"
            ? "bridge"
            : "asset";
        await api.uploadAsset(board.slug, target.beat, target.file, join);
        landed.push(`beat ${target.beat} ← ${target.file.name}`);
      } catch (problem) {
        failed.push(String(problem));
      }
      setBusy({ done: index + 1, total: targets.length });
    }
    setBusy(null);
    setPlaced(
      unused.length
        ? [...landed, `${unused.length} file(s) unused — no beat left needing a still`]
        : landed,
    );
    studio.setError(
      failed.length ? `${landed.length} of ${targets.length} stills landed. ${failed[0]}` : null,
    );
  };

  const setManual = (manual: boolean) =>
    void studio.guard(() => api.patchBoard(board.slug, { manual_stills: manual }));

  return (
    <div
      className="nodrag nopan space-y-1.5 rounded border border-[#26262e] bg-[#0d0d10] p-2"
      onDragOver={(event) => {
        event.preventDefault();
        setDropping(true);
      }}
      onDragLeave={() => setDropping(false)}
      onDrop={(event) => {
        event.preventDefault();
        void fill(event.dataTransfer.files);
      }}
    >
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] uppercase tracking-wide text-zinc-500">opening stills</span>
        <div className="ml-auto flex gap-1">
          {([false, true] as const).map((manual) => (
            <button
              key={String(manual)}
              onClick={() => setManual(manual)}
              className={`rounded px-1.5 py-0.5 text-[10px] ${
                board.manual_stills === manual
                  ? "bg-[#d99a4e] font-medium text-[#1a1208]"
                  : "bg-[#26262e] text-zinc-400 hover:bg-[#32323c]"
              }`}
              title={
                manual
                  ? "the stills are your own: every generate control disappears and the " +
                    "server refuses to generate for this reel at all"
                  : local
                    ? "stills are generated from each beat's asset_prompt by mflux on this " +
                      "machine — no quota, roughly 10–18 s each"
                    : "stills can be generated from each beat's asset_prompt — roughly five " +
                      "images per five-hour window"
              }
            >
              {manual ? "my own" : "generated"}
            </button>
          ))}
        </div>
      </div>

      <p className="text-[10px] leading-snug text-zinc-500">
        {needed.length === 0
          ? "every beat that opens a shot has its still"
          : `beat ${needed.join(", ")} still need${needed.length === 1 ? "s" : ""} one`}
        {board.manual_stills
          ? " · generation is off for this reel"
          : local
            ? " · generated locally by mflux, no quota"
            : " · generated by agy, ~5 per 5 hours"}
      </p>

      {/* Said here because this panel is where a missing image is usually noticed, but kept
          separate: reference pictures are not stills, and nothing on this panel can place
          them -- they go on their own node, where their order is visible. */}
      {board.refs_needed.length ? (
        <p className="text-[10px] leading-snug text-[#f59e0b]">
          beat {board.refs_needed.join(", ")} wait
          {board.refs_needed.length === 1 ? "s" : ""} on reference pictures — add those on the
          scene itself, up to {board.max_refs} each
        </p>
      ) : null}

      <input
        ref={picker}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        multiple
        className="hidden"
        onChange={(event) => {
          void fill(event.target.files);
          event.target.value = "";
        }}
      />
      <Button
        tone="quiet"
        className="w-full"
        disabled={Boolean(busy)}
        onClick={() => picker.current?.click()}
        title="pick or drop several images at once — costs no quota"
      >
        {busy
          ? `uploading ${busy.done}/${busy.total}…`
          : dropping
            ? "drop to fill the stills"
            : needed.length
              ? `⤒ fill ${needed.length} still${needed.length === 1 ? "" : "s"} from files`
              : "⤒ replace stills from files"}
      </Button>

      {placed.length ? (
        <div className="space-y-0.5">
          {placed.map((line) => (
            <p key={line} className="truncate text-[10px] text-[#4ade80]/80" title={line}>
              {line}
            </p>
          ))}
        </div>
      ) : (
        <p className="text-[10px] leading-snug text-zinc-600">
          Files fill the beats that need a still, in name order. Name one{" "}
          <code>beat3.png</code> to place it on that beat exactly.
        </p>
      )}
    </div>
  );
}

/**
 * Which beat each dropped file is meant for.
 *
 * Two rules, tried in order. If every file's name carries exactly one number that is a beat
 * on this board, and they are distinct, that is the mapping -- which is how you place a still
 * on a beat that currently continues from the one before, or replace one that is already
 * there. Otherwise the files fill the beats that still need a still, in name order.
 *
 * Anything left over is reported rather than applied: dropping a still on a beat nobody
 * nominated would silently turn a free continuation into a cut.
 */
function assign(files: File[], board: Board): { targets: Target[]; unused: File[] } {
  const sorted = [...files].sort((a, b) =>
    a.name.localeCompare(b.name, undefined, { numeric: true }),
  );
  const named = sorted.map((file) => {
    const numbers = (file.name.match(/\d+/g) ?? [])
      .map(Number)
      // A reference beat is not a target for a bulk fill: it has no keyframe slot to fill,
      // and uploading a still to it would swap its pictures for a cut. Its images go on the
      // node itself, where the order they land in is visible.
      .filter((value) =>
        board.beats.some((beat) => beat.n === value && beat.source !== "reference"),
      );
    // Ambiguous on purpose: "01-02.png" or a date stamp that happens to contain a beat
    // number is not a placement, so it falls through to the order rule below.
    return numbers.length === 1 ? numbers[0] : null;
  });
  const distinct = new Set(named.filter((value): value is number => value !== null));
  if (named.every((value) => value !== null) && distinct.size === named.length) {
    return { targets: sorted.map((file, index) => ({ beat: named[index]!, file })), unused: [] };
  }
  const needed = board.assets_needed;
  return {
    targets: sorted.slice(0, needed.length).map((file, index) => ({ beat: needed[index], file })),
    unused: sorted.slice(needed.length),
  };
}

interface Target {
  beat: number;
  file: File;
}
