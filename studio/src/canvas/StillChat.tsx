import { useState } from "react";
import { api } from "../api";
import { joinWarning, slotsLeft } from "../beat";
import type { Beat } from "../types";
import { useBusy, useStudio } from "../useStudio";
import { AssetChat } from "./AssetChat";

/**
 * The conversation about one still.
 *
 * The board's own chat panel edits the story — beats, joins, lengths. This edits a picture, and
 * it is a different conversation on purpose: the model is shown this still and everything the
 * still is drawn from, and what it writes back is this beat's `asset_prompt`, followed by a
 * re-render.
 *
 * The automatic review posts here too, so a node reads as the whole history of how its picture
 * got to be what it is: what was asked for, what the reviewer objected to, which turns ended in
 * a redraw. That is the thing which is baffling anywhere else — a still that came back different
 * from the prompt you can see — and obvious here.
 *
 * Collapsed by default on a node. A node is 240px wide and most of them are finished; the count
 * on the toggle is what makes an unread verdict findable without opening eight panels. `expanded`
 * is the modal, where there is room for the whole transcript and the toggle would be in the way.
 *
 * The panel itself is `AssetChat`, shared with the per-picture conversation. What stays here is
 * everything that makes it the STILL's: its turns, its endpoint, and the fact that a picture
 * attached to a note is kept on the beat — which is why this one can move the join and the
 * other cannot.
 */
export function StillChat({ beat, expanded = false }: { beat: Beat; expanded?: boolean }) {
  const studio = useStudio();
  const board = studio.board!;
  const [open, setOpen] = useState(false);
  const turns = beat.asset_chat ?? [];
  const busy = useBusy("still_chat", (detail) => detail.beat === beat.n);

  const panel = (
    <AssetChat
      turns={turns}
      busy={busy}
      expanded={expanded}
      onSend={(message, files) =>
        void studio.guard(() => api.stillChat(board.slug, beat.n, message, files))
      }
      placeholder="what should be different about this still?"
      empty={
        <>
          “her ears are too pointed”, “move the lamp to the left”, “same thing again, a different
          draw”. Attach a picture and the still is drawn from that too. The current still and all
          available beat references are sent to Gemini; the video is not touched.
        </>
      }
      attach={{
        // Against the per-beat budget, which is what the server enforces: two of the model's
        // nine slots are already spoken for on a scene that opens a shot.
        slotsLeft: slotsLeft(beat),
        // Attaching a picture is the same gesture as ⤒ add picture, consequence included — the
        // beat moves onto the reference join. One sentence, from the same place the tray's is.
        warning: joinWarning(beat),
        title:
          "show the model a picture — of the cast, the set, a prop. It is kept on this scene " +
          "as a reference picture, so the still is drawn from it and so is the clip" +
          (joinWarning(beat) ? `, and ${joinWarning(beat)}` : ""),
        fullTitle: `${board.max_refs} pictures is the model's limit — remove one first`,
      }}
      offline={
        studio.stillsBackend === "papercut"
          ? null
          : "the image server is down — the prompt will be rewritten, but nothing redrawn"
      }
    />
  );

  if (expanded) {
    return <div className="rounded border border-[#26262e]">{panel}</div>;
  }

  return (
    <div className="rounded border border-[#26262e]">
      <button
        onClick={() => setOpen((current) => !current)}
        className="nodrag flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left
          text-[10px] text-zinc-400 hover:bg-[#26262e]"
        title={
          "say what is wrong with this still and have it redrawn — the model looks at the " +
          "picture, rewrites this beat's prompt and sends the current still plus beat references " +
          "to Gemini"
        }
      >
        <span className="text-[#d99a4e]">✎</span>
        {busy ? "looking at this still…" : "talk about this still"}
        {turns.length ? <span className="ml-auto text-zinc-600">{turns.length}</span> : null}
      </button>
      {open ? panel : null}
    </div>
  );
}
