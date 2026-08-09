import { api } from "../api";
import type { Beat, Board } from "../types";
import { useStudio } from "../useStudio";

/**
 * Which of the reel's designs this scene contains.
 *
 * Toggles rather than a tray, and that is the difference between this and adding a picture. A
 * picture is uploaded TO one scene and belongs to it; a design already exists on the reel, and
 * the only question here is whether this shot is one of the shots it appears in. Nothing is
 * created, nothing is uploaded, and the answer is one set rather than a series of additions —
 * which is why the endpoint replaces rather than appends.
 *
 * It also carries no join warning, unlike every other control that adds an image to a scene. A
 * picture reaches a render only through the reference join, so storing one has to move the join
 * and say so. A bound design reaches every join: as a numbered picture where there are picture
 * slots, and as a sentence everywhere else. There is nothing to warn about because nothing
 * changes underneath you.
 */
export function StagingBind({ board, beat }: { board: Board; beat: Beat }) {
  const studio = useStudio();
  const bound = beat.staging ?? [];

  const toggle = (id: string) => {
    // Appended at the end when it goes in, so binding a design never renumbers the ones already
    // there — the same reason `uploadRefs` appends.
    const next = bound.includes(id) ? bound.filter((other) => other !== id) : [...bound, id];
    void studio.guard(() => api.bindStage(board.slug, beat.n, next));
  };

  if (!board.staging.length) {
    return (
      <div className="space-y-1">
        <span className="text-[10px] uppercase tracking-wide text-zinc-500">staging</span>
        <p className="text-[10px] leading-snug text-zinc-400">
          Nothing designed yet.{" "}
          <button
            onClick={() => studio.setStagingOpen(true)}
            className="text-zinc-600 underline hover:text-warm"
          >
            Design the cast and the sets
          </button>{" "}
          and every scene they appear in is conditioned on the same sheets.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-wide text-zinc-500">staging</span>
        <button
          onClick={() => studio.setStagingOpen(true)}
          className="ml-auto text-[10px] text-zinc-500 hover:text-warm"
          title="open the design bible"
        >
          edit designs
        </button>
      </div>

      <div className="flex flex-wrap gap-1">
        {board.staging.map((entry) => {
          const on = bound.includes(entry.id);
          return (
            <button
              key={entry.id}
              onClick={() => toggle(entry.id)}
              title={
                on
                  ? `${entry.role} — click to take it out of this scene`
                  : `put ${entry.name} in this scene`
              }
              className={`flex items-center gap-1.5 rounded-full py-0.5 pl-0.5 pr-2 text-[10px]
                transition-colors ${
                  on ? "bg-solid text-white" : "bg-soft text-zinc-600 hover:bg-softer"
                }`}
            >
              <span className="flex h-4 w-4 shrink-0 items-center justify-center overflow-hidden
                rounded-full bg-ink">
                {entry.sheet ? (
                  <img src={entry.sheet} alt="" className="h-full w-full object-cover" />
                ) : null}
              </span>
              {entry.name}
            </button>
          );
        })}
      </div>

      {/* What the binding actually did, in the two places it lands. Said rather than left to be
          inferred, because the two numbers differ on purpose and a set that reads as "bound" but
          reaches the still as prose is exactly the kind of thing worth saying out loud. */}
      {bound.length ? (
        <div className="space-y-0.5 text-[10px] leading-snug text-zinc-400">
          <p>
            {beat.source === "reference"
              ? `the clip is given ${beat.staging_refs} of ${bound.length} as pictures`
              : `this scene is on the ${beat.source} join, which takes no pictures at all`}
            {beat.staging_text ? (
              <>
                {" "}
                and the rest as words: <em>{beat.staging_text}</em>
              </>
            ) : (
              "."
            )}
          </p>
          <p>
            the still it opens on is given {beat.staging_still_refs}
            {beat.staging_still_text ? (
              <>
                {" "}
                and the rest as words: <em>{beat.staging_still_text}</em>
              </>
            ) : (
              "."
            )}
          </p>
        </div>
      ) : null}
    </div>
  );
}
