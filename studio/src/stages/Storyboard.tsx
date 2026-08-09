import { useState } from "react";
import { api } from "../api";
import { Design, KIND_LOOK } from "../canvas/StagingPanel";
import { JOIN_LOOK } from "../joins";
import { nextBinding, sceneList } from "../staging";
import type { Beat, Board, StageEntry, StageKind } from "../types";
import { useBusy, useDraft, useStudio } from "../useStudio";
import { Button, inputClass } from "../ui";
import { StagePage, WaitingOn } from "./parts";

/**
 * Stage two: what the film is made of, and how each shot is framed.
 *
 * Two things live here, in this order on purpose — the cast and the sets first, the panels
 * after. `panels._messages` already opens with "WHAT THIS FILM IS MADE OF AND WHO IS IN IT.
 * This is here so your panels name the right subject", and a named cast is what makes that
 * concrete. A *drawn* sheet pays off one stage later, in what the still is conditioned on, so
 * drawing can lag naming without hurting anything.
 *
 * Nothing on this page can mark a rendered beat stale, which is what makes it safe to press on
 * a reel that is already paid for: a panel reaches no renderer and is in no fingerprint, and a
 * design only enters one on the scenes that bind it.
 */
export function Storyboard() {
  const studio = useStudio();
  const board = studio.board!;
  const [sheet, setSheet] = useState(false);

  const writingPanels = useBusy("panel_write", () => true);
  const drawingPanels = useBusy("panel_draw", () => true);

  const withText = board.beats.filter((beat) => beat.panel?.trim());
  const written = withText.length;
  const drawn = board.beats.filter((beat) => beat.panel_url).length;
  const total = board.beats.length;

  // The design the cast strip has selected. Selecting one does two things at once: it opens the
  // design beside the grid, and it turns the grid into a checklist for that design — which is
  // the gesture this page exists to make possible. Binding one character across seven shots
  // used to be seven trips through the expanded-scene modal.
  const picked = board.staging.find((entry) => entry.id === studio.stagingPick) ?? null;

  const writeShots = () => void studio.guard(() => api.writePanels(board.slug));
  const drawPanels = () =>
    void studio.guard(() =>
      // Every scene with a shot written, rather than only the ones with no sketch: the button
      // says "redraw" once there are panels, and that is what redraw means.
      api.drawPanels(board.slug, drawn ? withText.map((beat) => beat.n) : undefined),
    );

  return (
    <StagePage
      stage="storyboard"
      title="Storyboard"
      blurb={`${board.staging.length} designs · ${written}/${total} shots written · ${drawn}/${total} drawn · free`}
      waiting={
        !total ? (
          <WaitingOn>Nothing to storyboard yet — write the script first.</WaitingOn>
        ) : !board.staging.length ? (
          <WaitingOn
            action={
              <Button tone="primary" onClick={() => studio.setStagingPick(null)}>
                ＋ design something
              </Button>
            }
          >
            Nothing designed yet — name the characters and sets that turn up in more than one
            scene. Those are the ones that drift, and the panels below name the right subject
            once the film has a cast.
          </WaitingOn>
        ) : !written ? (
          <WaitingOn
            action={
              <Button tone="primary" onClick={writeShots} disabled={writingPanels}>
                {writingPanels ? "writing…" : "✎ write the shots"}
              </Button>
            }
          >
            No shot grammar yet. The model writes the shot size, angle and camera move for every
            scene at once — it has to see them together to vary them.
          </WaitingOn>
        ) : drawn < written ? (
          <WaitingOn
            action={
              <Button tone="primary" onClick={drawPanels} disabled={drawingPanels}>
                {drawingPanels ? "drawing…" : "▦ draw the panels"}
              </Button>
            }
          >
            {written - drawn} shot{written - drawn === 1 ? "" : "s"} written but not sketched. A
            panel reaches no renderer, so this changes no scene's state.
          </WaitingOn>
        ) : (
          <WaitingOn
            tone="quiet"
            action={<Button onClick={() => studio.goStage("assets")}>→ Assets</Button>}
          >
            Every shot is written and drawn. Next is the still each one opens on.
          </WaitingOn>
        )
      }
    >
      <CastStrip board={board} picked={picked} />

      <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Button onClick={writeShots} disabled={writingPanels || !total}>
              {writingPanels ? "writing…" : written ? "✎ rewrite the shots" : "✎ write the shots"}
            </Button>
            <Button onClick={drawPanels} disabled={drawingPanels || !written}>
              {drawingPanels ? "drawing…" : drawn ? "▦ redraw the panels" : "▦ draw the panels"}
            </Button>
            <div className="ml-auto flex gap-1 rounded-full bg-ink p-0.5 text-[11px]">
              {(
                [
                  [false, "▦ grid"],
                  [true, "🗇 sheet"],
                ] as const
              ).map(([option, label]) => (
                <button
                  key={label}
                  onClick={() => setSheet(option)}
                  className={`rounded-full px-2.5 py-1 transition-colors ${
                    sheet === option ? "bg-solid text-white" : "text-zinc-500 hover:text-zinc-800"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {picked ? (
            <p className="mb-2.5 text-[11px] text-warm">
              Tick the scenes {picked.name} is in — {sceneList(board, picked.id)} so far.{" "}
              <button
                onClick={() => studio.setStagingPick(null)}
                className="underline hover:text-zinc-700"
              >
                done
              </button>
            </p>
          ) : null}

          {sheet ? <ContactSheet board={board} /> : <Grid board={board} picked={picked} />}
        </div>

        <div className="rounded-2xl border border-edge bg-panel">
          {picked ? (
            <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-2xl">
              <Design board={board} entry={picked} layout="column" />
            </div>
          ) : (
            <AddDesign board={board} />
          )}
        </div>
      </div>
    </StagePage>
  );
}

/**
 * Every design in the reel, as one row of chips.
 *
 * First on the page because it answers the question every panel below assumes: who is in this
 * film. Selecting one is the binding gesture — see `Grid`.
 */
function CastStrip({ board, picked }: { board: Board; picked: StageEntry | null }) {
  const studio = useStudio();
  if (!board.staging.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="mr-1 text-[10px] uppercase tracking-wide text-zinc-400">cast &amp; sets</span>
      {board.staging.map((entry) => {
        const on = entry.id === picked?.id;
        return (
          <button
            key={entry.id}
            onClick={() => studio.setStagingPick(on ? null : entry.id)}
            title={`${entry.role} — ${sceneList(board, entry.id)}. Click to bind it across the reel`}
            className={`flex items-center gap-1.5 rounded-full py-0.5 pl-0.5 pr-2.5 text-[11px]
              transition-colors ${on ? "bg-solid text-white" : "bg-soft text-zinc-700 hover:bg-softer"}`}
          >
            <span className="flex h-5 w-5 shrink-0 items-center justify-center overflow-hidden rounded-full bg-ink text-[10px]">
              {entry.sheet ? (
                <img src={entry.sheet} alt="" className="h-full w-full object-cover" />
              ) : (
                KIND_LOOK[entry.kind].icon
              )}
            </span>
            {entry.name}
          </button>
        );
      })}
      <button
        onClick={() => studio.setStagingPick(null)}
        title="free: this makes an entry, it does not draw anything"
        className="rounded-full bg-soft px-2.5 py-1 text-[11px] text-zinc-500 transition-colors hover:bg-softer"
      >
        ＋
      </button>
    </div>
  );
}

/** Mint one design. Shown in the right column whenever nothing is selected. */
function AddDesign({ board }: { board: Board }) {
  const studio = useStudio();
  const [kind, setKind] = useState<StageKind>("character");
  const [name, setName] = useState("");
  const full = board.staging.length >= board.max_staging;

  const add = () => {
    const trimmed = name.trim();
    if (!trimmed || full) return;
    setName("");
    void studio.guard(async () => {
      const created = await api.addStage(board.slug, { kind, name: trimmed });
      // Selected straight away, because the next thing anyone does is say what it looks like.
      studio.setStagingPick(created.id);
      return created;
    });
  };

  return (
    <div className="space-y-2.5 p-3">
      <p className="text-[10px] uppercase tracking-wide text-zinc-400">design something</p>
      <div className="flex gap-1 rounded-full bg-ink p-0.5 text-[10px]">
        {board.stage_kinds.map((option) => (
          <button
            key={option}
            onClick={() => setKind(option)}
            title={KIND_LOOK[option].hint}
            className={`flex-1 rounded-full px-1.5 py-1 transition-colors ${
              kind === option ? "bg-solid text-white" : "text-zinc-500 hover:text-zinc-800"
            }`}
          >
            {KIND_LOOK[option].label}
          </button>
        ))}
      </div>
      <input
        className={inputClass}
        value={name}
        onChange={(event) => setName(event.target.value)}
        onKeyDown={(event) => event.key === "Enter" && add()}
        placeholder={kind === "environment" ? "the clearing" : "Vera"}
      />
      <Button tone="primary" className="w-full" onClick={add} disabled={full || !name.trim()}>
        {full ? `${board.max_staging} is the ceiling` : "add"}
      </Button>
      <p className="text-[10px] leading-relaxed text-zinc-400">
        The name is what the prompts call it, so make it the one the action lines use. A design
        bound to a scene reaches its clip as a numbered picture and its still as the same image —
        so the wolf in scene 2 and the wolf in scene 6 are one wolf rather than two readings of
        the same sentence.
      </p>
    </div>
  );
}

/** The panels as cards, and — while a design is selected — as a checklist for it. */
function Grid({ board, picked }: { board: Board; picked: StageEntry | null }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {board.beats.map((beat) => (
        <ShotCard key={beat.n} beat={beat} board={board} picked={picked} />
      ))}
      {!board.beats.length ? (
        <p className="text-[12px] leading-relaxed text-zinc-400">No scenes yet.</p>
      ) : null}
    </div>
  );
}

function ShotCard({
  beat,
  board,
  picked,
}: {
  beat: Beat;
  board: Board;
  picked: StageEntry | null;
}) {
  const studio = useStudio();
  const look = JOIN_LOOK[beat.source];
  const bound = beat.staging ?? [];
  const drawing = useBusy(
    "panel_draw",
    (detail) => !Array.isArray(detail.beats) || detail.beats.includes(beat.n),
  );
  const writing = useBusy(
    "panel_write",
    (detail) => !Array.isArray(detail.beats) || detail.beats.includes(beat.n),
  );

  const shot = useDraft(beat.panel ?? "", (next) =>
    void studio.guard(() => api.patchBeat(board.slug, beat.n, { panel: next })),
  );

  const binding = Boolean(picked);
  const on = picked ? bound.includes(picked.id) : false;

  return (
    <div
      className={`overflow-hidden rounded-2xl border bg-panel transition-colors ${
        binding && on ? "border-solid" : "border-edge"
      }`}
    >
      <button
        onClick={() =>
          picked
            ? void studio.guard(() =>
                api.bindStage(board.slug, beat.n, nextBinding(bound, picked.id)),
              )
            : studio.setExpanded(beat.n)
        }
        className="relative block w-full"
        title={
          picked
            ? on
              ? `take ${picked.name} out of scene ${beat.n}`
              : `put ${picked.name} in scene ${beat.n}`
            : "open the whole scene"
        }
      >
        {beat.panel_url ? (
          // object-contain: the framing IS the content of a panel, so cropping one to fill a
          // tile throws away the thing it was drawn to show.
          <img
            src={beat.panel_url}
            alt={`panel ${beat.n}`}
            className="h-52 w-full bg-ink object-contain"
          />
        ) : (
          <div className="flex h-52 w-full items-center justify-center border-b border-dashed border-edge bg-ink text-[11px] text-zinc-400">
            {drawing ? "drawing…" : beat.panel?.trim() ? "not drawn yet" : "no shot written"}
          </div>
        )}
        {binding ? (
          <span
            className={`absolute left-2 top-2 flex h-6 w-6 items-center justify-center rounded-full
              text-[12px] ${on ? "bg-solid text-white" : "bg-panel/90 text-zinc-400"}`}
          >
            {on ? "✓" : "＋"}
          </span>
        ) : null}
      </button>

      <div className="space-y-1.5 px-3 py-2">
        <div className="flex items-center gap-2 text-[10px] text-zinc-400">
          <span className="font-medium text-zinc-600">scene {beat.n}</span>
          <span>{beat.actual_seconds.toFixed(1)}s</span>
          <span className={look.tone} title={look.hint}>
            {look.short}
          </span>
          <span className="ml-auto flex items-center gap-1.5">
            <button
              onClick={() => {
                shot.flush();
                void studio.guard(() => api.drawPanel(board.slug, beat.n));
              }}
              disabled={drawing || writing || !shot.draft.trim()}
              title={
                shot.draft.trim()
                  ? beat.panel_url
                    ? "draw this panel again"
                    : "draw this panel"
                  : "write the shot first — there is nothing for a sketch to be a drawing of"
              }
              className="hover:text-warm disabled:opacity-30"
            >
              ✦
            </button>
            {/* One shot rewritten rather than the whole reel. The default stays all-of-them,
                because the sizes only mean anything judged against each other — but one wrong
                panel should not cost a re-roll of the film. */}
            <button
              onClick={() => void studio.guard(() => api.writePanels(board.slug, [beat.n]))}
              disabled={writing || drawing}
              title="have the model rewrite just this shot's grammar"
              className="hover:text-warm disabled:opacity-30"
            >
              ✎
            </button>
            {beat.panel_url ? (
              <button
                onClick={() => void studio.guard(() => api.removePanel(board.slug, beat.n))}
                disabled={drawing}
                title="throw this panel away. Nothing is conditioned on it"
                className="hover:text-red-600 disabled:opacity-30"
              >
                ✕
              </button>
            ) : null}
            <button
              onClick={() => studio.setExpanded(beat.n)}
              title="open the whole scene"
              className="hover:text-zinc-700"
            >
              ⤢
            </button>
          </span>
        </div>

        <textarea
          className={`${inputClass} thin h-14 leading-relaxed`}
          value={shot.draft}
          onChange={(event) => shot.change(event.target.value)}
          onBlur={shot.flush}
          placeholder="medium shot, eye level, locked off — the subject at frame left"
          title="shot size, angle, camera move. It reaches no renderer, so editing it marks
            nothing stale."
        />

        {bound.length ? (
          <div className="flex flex-wrap gap-1">
            {bound.map((id) => {
              const entry = board.staging.find((candidate) => candidate.id === id);
              if (!entry) return null;
              return (
                <button
                  key={id}
                  onClick={() =>
                    void studio.guard(() =>
                      api.bindStage(board.slug, beat.n, nextBinding(bound, id)),
                    )
                  }
                  title={`${entry.role} — click to take it out of this scene`}
                  className="rounded-full bg-soft px-2 py-0.5 text-[10px] text-zinc-600 transition-colors hover:bg-softer"
                >
                  {entry.name}
                </button>
              );
            })}
            {/* The short form of the two sentences `StagingBind` prints. The asymmetry is the
                feature — a set is bound and still reaches the still as prose — so it must not
                quietly disappear at grid scale. */}
            <span className="w-full text-[10px] leading-snug text-zinc-400">
              clip: {beat.staging_refs} of {bound.length} as pictures · still:{" "}
              {beat.staging_still_refs}
              {beat.staging_still_text ? ", the rest as words" : ""}
            </span>
          </div>
        ) : null}
      </div>
    </div>
  );
}

/**
 * The whole film read at once.
 *
 * Built from the individual panels rather than from `board.panel_sheet`, so a redrawn panel
 * updates immediately — and so it is clickable. The PNG stays as what it is: a file you can send
 * someone. Five medium shots in a row is invisible in a column and unmissable in a grid, which
 * is the only reason a contact sheet exists.
 */
function ContactSheet({ board }: { board: Board }) {
  const studio = useStudio();
  const drawn = board.beats.filter((beat) => beat.panel_url);
  if (!drawn.length) {
    return (
      <p className="text-[12px] leading-relaxed text-zinc-400">
        No panels drawn yet — there is nothing to read as a sheet.
      </p>
    );
  }
  return (
    <div>
      <div className="grid grid-cols-3 gap-2 rounded-2xl border border-edge bg-panel p-3">
        {board.beats.map((beat) => (
          <button
            key={beat.n}
            onClick={() => studio.setExpanded(beat.n)}
            className="text-left"
            title={beat.panel || beat.scene}
          >
            {beat.panel_url ? (
              <img
                src={beat.panel_url}
                alt={`panel ${beat.n}`}
                className="aspect-[9/16] w-full rounded-lg bg-ink object-contain"
              />
            ) : (
              <div className="flex aspect-[9/16] w-full items-center justify-center rounded-lg border border-dashed border-edge bg-ink text-[10px] text-zinc-300">
                —
              </div>
            )}
            <div className="mt-1 text-center text-[10px] text-zinc-400">
              {beat.n} · {beat.actual_seconds.toFixed(0)}s ·{" "}
              <span className={JOIN_LOOK[beat.source].tone}>
                {JOIN_LOOK[beat.source].short}
              </span>
            </div>
          </button>
        ))}
      </div>
      {board.panel_sheet ? (
        <button
          onClick={() => window.open(board.panel_sheet!, "_blank")}
          className="mt-2 text-[11px] text-zinc-400 underline hover:text-zinc-700"
          title="every panel on one numbered PNG — a file you can send someone"
        >
          the sheet as one file
        </button>
      ) : null}
    </div>
  );
}
