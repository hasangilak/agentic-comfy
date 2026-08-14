import { useRef, useState } from "react";
import { api } from "../api";
import { castRef, stillPictures, type PictureRef } from "../beat";
import { AddPicture } from "../canvas/AddPicture";
import { FillStills } from "../canvas/FillStills";
import { PromptField } from "../canvas/Mentions";
import { KIND_LOOK } from "../canvas/StagingPanel";
import { StillChat } from "../canvas/StillChat";
import { AgentTurns } from "../panels/CrewPanel";
import { JOIN_LOOK } from "../joins";
import {
  DEFAULT_GEMINI_IMAGE_MODEL,
  DEFAULT_GEMINI_IMAGE_SIZE,
  GEMINI_IMAGE_MODELS,
  GEMINI_IMAGE_SIZES,
  type AssetTurn,
  type Beat,
  type Board,
  type GeminiImageModel,
  type GeminiImageSize,
  type Lens,
} from "../types";
import { useBusy, useDraft, useStudio } from "../useStudio";
import { Button, inputClass } from "../ui";
import { StagePage, WaitingOn } from "./parts";
import { stillsAllowed } from "../route";

/**
 * Stage three: the still every shot opens on, and what each one is drawn from.
 *
 * The hard part of this stage was invisible before it existed. `Board.still_pictures` is
 * identity-sheets first (or the cast still when those are missing), capped at four, and a set
 * that fits the cap is a picture — and the only thing that showed any of it was the @-mention
 * menu. `beat.ts`'s `stillPictures` already mirrors that method line for line, so the
 * conditioning strip below is a reading of what the model is actually handed rather than a
 * second guess at it. That is the whole feature, and it needed nothing new from the server.
 *
 * There is deliberately no board conversation on this stage. The board agent cannot see a
 * picture; `stills.converse` can, and it is the panel on the right.
 */
export function Assets() {
  const studio = useStudio();
  const board = studio.board!;
  const missing = board.assets_needed;
  const offline = studio.stillsBackend !== "papercut";
  const [model, setModel] = useState<GeminiImageModel>(DEFAULT_GEMINI_IMAGE_MODEL);
  const [size, setSize] = useState<GeminiImageSize>(DEFAULT_GEMINI_IMAGE_SIZE);
  const [crewBusy, setCrewBusy] = useState(false);

  const generating = useBusy("asset", () => true);
  const crewJob = useBusy("crew", () => true);
  const agentJob = useBusy("agent", () => true);
  const job = studio.activeJob?.kind === "asset" ? studio.activeJob : null;

  // Every scene that opens on a still of its own. A plain continuation has none and needs none.
  const shots = board.beats.filter((beat) => beat.source !== "chain");
  const [at, setAt] = useState<number | null>(null);
  const picked = shots.find((beat) => beat.n === at) ?? shots.find((b) => missing.includes(b.n))
    ?? shots[0] ?? null;

  // The scene whose still BECOMES the reel's cast reference. `stills.generate` renders and
  // reviews it entirely alone before anything else starts, because rejecting it after the batch
  // would replace the reference every other still had just been matched against. Derivable here
  // from the same fact the server uses, so the page can say it BEFORE the run rather than only
  // in the job log afterwards.
  const defining = board.reference ? null : (shots.find((beat) => !castRef(beat))?.n ?? null);
  const rest = missing.filter((n) => n !== defining);

  const done = new Set(board.crew?.done ?? []);
  const awaiting = board.crew?.awaiting ?? null;
  const canGenerate = stillsAllowed(board);
  const atStillsGate = !missing.length && (awaiting === "inspect" || done.has("stills"))
    && !done.has("inspect");
  const atInspectGate = !missing.length && done.has("inspect");
  const wantsStillsCrew = Boolean(missing.length) && (awaiting === "stills" || !done.has("stills"));

  const generate = (beats?: number[]) =>
    void studio.guard(() => api.assets(board.slug, beats, { model, imageSize: size }));

  const runPhase = (phase: string) => {
    setCrewBusy(true);
    void studio
      .guard(() => api.runCrew(board.slug, { stage: "assets", phase }))
      .finally(() => setCrewBusy(false));
  };

  const busy = crewBusy || crewJob || agentJob;

  return (
    <StagePage
      stage="assets"
      title="Assets"
      blurb={
        board.manual_stills
          ? "your own stills — nothing here generates"
          : `${shots.length - missing.length}/${shots.length} drawn · cents each · one at a time`
      }
      waiting={
        board.manual_stills ? (
          <WaitingOn tone="quiet">
            This reel supplies its own stills. Every generate control is off and the server
            refuses to spend on one.
          </WaitingOn>
        ) : !canGenerate ? (
          <WaitingOn
            action={<Button onClick={() => studio.goStage("storyboard")}>→ Storyboard</Button>}
          >
            Every shot needs a storyboard panel, and a gated reel needs the cast locked against
            those panels, before a still is drawn. Uploads still work.
          </WaitingOn>
        ) : defining !== null && missing.includes(defining) ? (
          <WaitingOn
            action={
              <Button
                tone="primary"
                onClick={() => generate([defining])}
                disabled={offline || generating}
              >
                {generating ? "drawing…" : `✦ draw the cast — scene ${defining}`}
              </Button>
            }
          >
            Nothing is pinned as the cast yet, so scene {defining}'s still becomes it — every
            other still in the reel is then matched against that one. It is drawn and judged on
            its own first, before the rest, so you can reject it while rejecting it is cheap.
          </WaitingOn>
        ) : wantsStillsCrew && missing.length ? (
          <WaitingOn
            action={
              <div className="flex flex-wrap gap-2">
                <Button
                  tone="primary"
                  onClick={() => runPhase("stills")}
                  disabled={offline || busy}
                >
                  {busy ? "working…" : "Run stills crew"}
                </Button>
                <Button
                  onClick={() => generate(rest.length === missing.length ? undefined : rest)}
                  disabled={offline || generating}
                >
                  {generating ? "drawing…" : `✦ generate ${missing.length}`}
                </Button>
              </div>
            }
          >
            {missing.length} scene{missing.length === 1 ? "" : "s"} without the still{" "}
            {missing.length === 1 ? "it opens on" : "they open on"}
            {offline ? " — and the image server is not answering, so these have to be uploads." : "."}
            {job?.beat ? ` Drawing scene ${job.beat} now.` : ""} The crew draws and reviews; the
            generate button skips straight to the batch.
          </WaitingOn>
        ) : missing.length ? (
          <WaitingOn
            action={
              <Button
                tone="primary"
                onClick={() => generate(rest.length === missing.length ? undefined : rest)}
                disabled={offline || generating}
              >
                {generating ? "drawing…" : `✦ generate ${missing.length}`}
              </Button>
            }
          >
            {missing.length} scene{missing.length === 1 ? "" : "s"} without the still{" "}
            {missing.length === 1 ? "it opens on" : "they open on"}
            {offline ? " — and the image server is not answering, so these have to be uploads." : "."}
            {job?.beat ? ` Drawing scene ${job.beat} now.` : ""}
          </WaitingOn>
        ) : atStillsGate ? (
          <WaitingOn
            action={
              <Button tone="primary" onClick={() => runPhase("inspect")} disabled={busy}>
                {busy ? "working…" : "Run inspectors"}
              </Button>
            }
          >
            Every shot has a still. Mise looks at each still beside the sheets and the panel —
            the three check lenses report and suggest; they never re-render.
          </WaitingOn>
        ) : atInspectGate ? (
          <WaitingOn
            tone="quiet"
            action={<Button onClick={() => studio.goStage("studio")}>→ Studio</Button>}
          >
            Inspectors have filed their verdicts below. Apply the fixes you want, then open the
            studio for the chain and the render.
          </WaitingOn>
        ) : (
          <WaitingOn
            tone="quiet"
            action={
              <div className="flex flex-wrap gap-2">
                {!done.has("inspect") ? (
                  <Button onClick={() => runPhase("inspect")} disabled={busy}>
                    {busy ? "working…" : "Run inspectors"}
                  </Button>
                ) : null}
                <Button onClick={() => studio.goStage("studio")}>→ Studio</Button>
              </div>
            }
          >
            Every shot has the still it opens on. Next is the chain, the price and the render.
          </WaitingOn>
        )
      }
    >
      <Conditioning board={board} />

      {board.manual_stills ? (
        <div className="mt-4 max-w-md">
          <FillStills />
        </div>
      ) : (
        <div className="mt-4 flex flex-wrap items-center gap-2 text-[11px] text-zinc-500">
          <span>drawn by</span>
          <select
            value={model}
            onChange={(event) => setModel(event.target.value as GeminiImageModel)}
            className="rounded-xl border border-edge bg-ink px-2 py-1.5 text-[11px] text-zinc-700 outline-none"
            title={GEMINI_IMAGE_MODELS.find((m) => m.id === model)?.blurb}
          >
            {GEMINI_IMAGE_MODELS.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
          <select
            value={size}
            onChange={(event) => setSize(event.target.value as GeminiImageSize)}
            className="rounded-xl border border-edge bg-ink px-2 py-1.5 text-[11px] text-zinc-700 outline-none"
          >
            {GEMINI_IMAGE_SIZES.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
          <span className="text-zinc-400">
            the batch default; a scene keeps whatever it was last drawn with
          </span>
        </div>
      )}

      <div className="mt-5 grid gap-4 xl:grid-cols-[13rem_minmax(0,1fr)_22rem]">
        <div className="space-y-0.5">
          {shots.map((beat) => (
            <ShotRow
              key={beat.n}
              beat={beat}
              here={beat.n === picked?.n}
              defining={beat.n === defining}
              onPick={() => setAt(beat.n)}
            />
          ))}
        </div>

        {picked ? (
          <>
            <Still
              beat={picked}
              board={board}
              defining={picked.n === defining}
              onGenerate={generate}
              canGenerate={canGenerate}
            />
            <Talk beat={picked} board={board} />
          </>
        ) : (
          <p className="text-[12px] text-zinc-400">No scene opens on a still yet.</p>
        )}
      </div>
    </StagePage>
  );
}

/**
 * What the whole reel's stills are conditioned on, said once at reel level.
 *
 * The `MAX_STILL_REFS` consequence belongs here rather than repeated on every card: four
 * slots, identity first, a set that fits is a picture and one that does not is told in words.
 * That is `Board.still_pictures`, and it is the one asymmetry a director has to know.
 */
function Conditioning({ board }: { board: Board }) {
  const studio = useStudio();
  const picker = useRef<HTMLInputElement>(null);
  const asPictures = board.staging.filter((entry) => entry.kind !== "environment");
  const sets = board.staging.filter((entry) => entry.kind === "environment");

  return (
    <div className="flex flex-wrap items-start gap-4 rounded-2xl border border-edge bg-ink p-3">
      <div className="flex items-start gap-2.5">
        {board.reference ? (
          <img
            src={board.reference}
            alt="cast reference"
            className="h-20 w-12 shrink-0 rounded-lg object-cover"
          />
        ) : (
          <div className="flex h-20 w-12 shrink-0 items-center justify-center rounded-lg border border-dashed border-edge text-[9px] text-zinc-400">
            none
          </div>
        )}
        <div className="max-w-56">
          <p className="text-[10px] uppercase tracking-wide text-zinc-500">cast reference</p>
          <p className="mt-0.5 text-[10px] leading-snug text-zinc-500">
            {board.manual_stills
              ? "only used when a still is generated, which is off for this reel"
              : !board.reference
                ? "the first still generated will set the look"
                : board.reference_explicit
                  ? "every new still is matched to this image"
                  : "using the first scene's still — every new still is matched to it"}
          </p>
          <div className="mt-1 flex gap-2 text-[10px]">
            <button onClick={() => picker.current?.click()} className="text-zinc-600 hover:text-warm">
              {board.reference_explicit ? "replace" : "pin my own"}
            </button>
            {board.reference_explicit ? (
              <button
                onClick={() => void studio.guard(() => api.clearReference(board.slug))}
                className="text-zinc-500 hover:text-red-600"
              >
                clear
              </button>
            ) : null}
          </div>
          <input
            ref={picker}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) void studio.guard(() => api.uploadReference(board.slug, file));
            }}
          />
        </div>
      </div>

      {board.staging.length ? (
        <div className="min-w-0 flex-1 space-y-1.5">
          <p className="text-[10px] uppercase tracking-wide text-zinc-500">
            the designs a bound scene is drawn from
          </p>
          <div className="flex flex-wrap items-center gap-1.5">
            {asPictures.map((entry) => (
              <button
                key={entry.id}
                onClick={() => {
                  studio.setStagingPick(entry.id);
                  studio.goStage("storyboard");
                }}
                title={entry.role}
                className="flex items-center gap-1.5 rounded-full bg-soft py-0.5 pl-0.5 pr-2.5 text-[10px] text-zinc-700 hover:bg-softer"
              >
                <span className="flex h-5 w-5 items-center justify-center overflow-hidden rounded-full bg-panel">
                  {entry.sheet ? (
                    <img src={entry.sheet} alt="" className="h-full w-full object-cover" />
                  ) : (
                    KIND_LOOK[entry.kind].icon
                  )}
                </span>
                {entry.name}
              </button>
            ))}
            {asPictures.length ? (
              <span className="text-[10px] text-zinc-400">reach the still as pictures</span>
            ) : null}
          </div>
          {sets.length ? (
            <div className="flex flex-wrap items-center gap-1.5">
              {sets.map((entry) => (
                <button
                  key={entry.id}
                  onClick={() => {
                    studio.setStagingPick(entry.id);
                    studio.goStage("storyboard");
                  }}
                  title={entry.role}
                  className="flex items-center gap-1.5 rounded-full bg-soft py-0.5 pl-0.5 pr-2.5 text-[10px] text-zinc-700 hover:bg-softer"
                >
                  <span className="flex h-5 w-5 items-center justify-center overflow-hidden rounded-full bg-panel">
                    {entry.sheet ? (
                      <img src={entry.sheet} alt="" className="h-full w-full object-cover" />
                    ) : (
                      KIND_LOOK[entry.kind].icon
                    )}
                  </span>
                  {entry.name}
                </button>
              ))}
              <span className="text-[10px] text-zinc-400">
                reach the still as a picture when they fit the {board.max_still_refs}-slot cap,
                otherwise as words
              </span>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** One scene in the list, with the pip the review earned it. */
function ShotRow({
  beat,
  here,
  defining,
  onPick,
}: {
  beat: Beat;
  here: boolean;
  defining: boolean;
  onPick: () => void;
}) {
  const busy = useBusy("asset", (detail) => {
    const beats = detail.beats;
    return !Array.isArray(beats) || beats.includes(beat.n);
  });
  const mark = pip(beat);
  return (
    <button
      onClick={onPick}
      className={`flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left transition-colors ${
        here ? "bg-soft" : "hover:bg-hover"
      }`}
    >
      <span className={`w-3 shrink-0 text-center text-[11px] ${mark.tone}`} title={mark.hint}>
        {busy ? "◐" : mark.glyph}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[12px] text-zinc-800">scene {beat.n}</span>
        <span className="block truncate text-[10px] text-zinc-400">
          {defining ? "defines the look" : JOIN_LOOK[beat.source].short}
        </span>
      </span>
      {beat.asset ? (
        <img src={beat.asset} alt="" className="h-9 w-6 shrink-0 rounded object-cover" />
      ) : null}
    </button>
  );
}

/** The three lenses a crew's checkers file under. Same words `critique.LENSES` uses. */
const LENSES: Lens[] = ["style", "blocking", "story"];
const isLens = (role: string): role is Lens => (LENSES as string[]).includes(role);

/**
 * The review's last word about this still, as one character.
 *
 * Off `verdict`, which `stills.remember` stamps on every review turn — not off the copy.
 * A board written before that key exists reads as "no verdict", which is the truth about it.
 *
 * The crew's three checkers stamp the same key, and this deliberately skips them. Their
 * verdicts are a PANEL — three lenses answering three questions about one picture, filed in
 * whatever order the cast ran — so "the last one" is not a summary of them, it is whichever
 * lens happened to go last. `LensPanel` below shows all three; this stays what it was, the
 * automatic review's own word.
 */
function pip(beat: Beat): { glyph: string; tone: string; hint: string } {
  if (!beat.asset) return { glyph: "○", tone: "text-warm", hint: "no still yet" };
  const judged = [...(beat.asset_chat ?? [])]
    .reverse()
    .find((turn) => turn.verdict && !isLens(turn.role));
  if (!judged) return { glyph: "·", tone: "text-zinc-300", hint: "not reviewed" };
  if (judged.verdict === "pass") {
    return { glyph: "✓", tone: "text-live", hint: "the reviewer says it matches the reel" };
  }
  if (judged.verdict === "kept") {
    return {
      glyph: "!",
      tone: "text-stale",
      hint: "the reviewer objected but had no different prompt to offer",
    };
  }
  return { glyph: "⚠", tone: "text-stale", hint: "the reviewer rewrote the prompt and redrew it" };
}

/** The still at judging size, and — the point of this stage — what it was drawn from. */
function Still({
  beat,
  board,
  defining,
  onGenerate,
  canGenerate,
}: {
  beat: Beat;
  board: Board;
  defining: boolean;
  onGenerate: (beats?: number[]) => void;
  canGenerate: boolean;
}) {
  const studio = useStudio();
  const picker = useRef<HTMLInputElement>(null);
  const busy = useBusy("asset", (detail) => {
    const beats = detail.beats;
    return !Array.isArray(beats) || beats.includes(beat.n);
  });
  const drawn = stillPictures(beat, board.staging ?? []);
  const offline = studio.stillsBackend !== "papercut";
  const rewritten = twiceRewritten(beat.asset_chat ?? []);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-center rounded-2xl border border-edge bg-ink p-3">
        {beat.asset ? (
          <img
            src={beat.asset}
            alt={`still ${beat.n}`}
            className="max-h-[26rem] rounded-xl object-contain"
          />
        ) : (
          <div className="flex h-72 items-center justify-center text-[11px] text-zinc-400">
            no still yet
          </div>
        )}
      </div>

      {defining ? (
        <p className="rounded-xl border border-warm/30 bg-warm/5 px-3 py-2 text-[10px] leading-relaxed text-warm">
          This scene defines the look. Its still becomes the reel's cast reference, and every
          other still is then matched against it — so it is drawn and judged on its own, before
          the rest of the batch.
        </p>
      ) : null}

      {/* Four affordances where there was one ✦, because they are four different intents with
          four different prices. "another take" is the common one and the only free one: the
          prompt is unchanged and the seed moves, which is what `papercut.generate(seed=…)` is
          for — without it a re-render comes back byte-identical. */}
      {board.manual_stills ? null : (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            tone="primary"
            onClick={() => onGenerate([beat.n])}
            disabled={offline || busy || !canGenerate}
            title={
              !canGenerate
                ? "write every storyboard panel, and lock the cast, before a still is drawn"
                : beat.asset
                ? "draw it again from the same prompt, with the seed moved — no model turn"
                : "draw this still"
            }
          >
            {busy ? "drawing…" : beat.asset ? "↻ another take" : "✦ draw it"}
          </Button>
          <Button tone="ghost" onClick={() => picker.current?.click()}>
            ⤒ use my own
          </Button>
          <AddPicture beat={beat} />
          <input
            ref={picker}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) {
                void studio.guard(() =>
                  api.uploadAsset(board.slug, beat.n, file, beat.source),
                );
              }
            }}
          />
        </div>
      )}

      {rewritten ? (
        <p className="text-[10px] leading-relaxed text-stale">
          Twice rewritten by the reviewer. That usually means the style bible is the problem
          rather than this prompt —{" "}
          <button
            onClick={() => studio.goStage("script")}
            className="underline hover:text-zinc-700"
          >
            read it on the Script stage
          </button>
          .
        </p>
      ) : null}

      <LensPanel beat={beat} slug={board.slug} />

      <div className="space-y-2 rounded-2xl border border-edge bg-panel p-3">
        <p className="text-[10px] uppercase tracking-wide text-zinc-500">drawn from</p>
        {drawn.length ? (
          <div className="space-y-1.5">
            {drawn.map((picture, at) => (
              <DrawnFrom key={at} picture={picture} beat={beat} />
            ))}
          </div>
        ) : (
          <p className="text-[10px] leading-relaxed text-zinc-400">
            Nothing but the prompt. Neither the cast reference nor a bound design reaches this
            scene's still yet.
          </p>
        )}
        {beat.staging_still_text ? (
          <p className="text-[10px] leading-relaxed text-zinc-500">
            and told in words: <em>{beat.staging_still_text}</em>
          </p>
        ) : null}
        {beat.source !== "reference" ? (
          <p className="text-[10px] leading-relaxed text-zinc-400">
            This scene is on the {beat.source} join, so its own pictures and bound designs reach
            the still as words rather than as images — only the cast reference goes over.
          </p>
        ) : null}
      </div>
    </div>
  );
}

/** One entry of the conditioning strip, with what it is and how to take it away. */
function DrawnFrom({ picture, beat }: { picture: PictureRef; beat: Beat }) {
  const studio = useStudio();
  const board = studio.board!;
  return (
    <div
      className={`flex items-start gap-2 rounded-xl px-1.5 py-1 ${
        picture.unavailable ? "opacity-45" : ""
      }`}
    >
      {picture.url ? (
        <img src={picture.url} alt="" className="h-10 w-10 shrink-0 rounded-lg object-cover" />
      ) : (
        <div className="h-10 w-10 shrink-0 rounded-lg bg-softer" />
      )}
      <div className="min-w-0 flex-1">
        <p className="text-[10px] text-zinc-600">
          <span className="text-zinc-400">{picture.tag}</span> — {picture.label}
        </p>
        <p className="truncate text-[10px] text-zinc-400" title={picture.note}>
          {picture.note || "nothing said about it"}
        </p>
        {picture.unavailable ? (
          <p className="text-[10px] leading-snug text-stale">
            {picture.unavailable}. Remove one above it, or unbind a design, to make room.
          </p>
        ) : null}
      </div>
      {picture.index !== null ? (
        <button
          onClick={() =>
            void studio.guard(() => api.removeRef(board.slug, beat.n, picture.index!))
          }
          title="remove this picture from the scene"
          className="shrink-0 text-[11px] text-zinc-300 hover:text-red-600"
        >
          ×
        </button>
      ) : null}
    </div>
  );
}

/**
 * What the crew's three checkers made of this still.
 *
 * Three lenses, three questions, one picture — craft, staging, story. They are shown together
 * and never as a running total, because they fail independently: a still can be flawless clay
 * and the wrong moment, or the right moment blocked backwards. A single "reviewed ✓" over the
 * three would be true of none of them.
 *
 * **A failing lens carries a suggested fix and nothing else happens.** No re-render, no prompt
 * rewrite — the checkers report and the director decides, which is the bound `critique.py`
 * exists to keep. So the fix is the actionable part of this panel and is shown in full.
 *
 * The latest verdict per lens rather than every one, because a still re-checked after a fix
 * should read as fixed. Everything is already in `asset_chat`, in order, for the transcript
 * below to show — this is a summary of it, not a second copy.
 */
function LensPanel({ beat, slug }: { beat: Beat; slug: string }) {
  const studio = useStudio();
  const [busy, setBusy] = useState<Lens | null>(null);
  const latest = new Map<Lens, AssetTurn>();
  for (const turn of beat.asset_chat ?? []) {
    if (isLens(turn.role) && turn.verdict) latest.set(turn.role, turn);
  }

  async function inspect(lens: Lens) {
    setBusy(lens);
    try {
      await studio.guard(() => api.inspectStill(slug, beat.n, lens));
      await studio.refreshBoard(slug);
    } finally {
      setBusy(null);
    }
  }

  if (!beat.asset) return null;

  return (
    <div className="space-y-1.5 rounded-2xl border border-edge bg-panel p-3">
      <div className="flex items-baseline gap-2">
        <p className="text-[10px] uppercase tracking-wide text-zinc-500">the crew looked at it</p>
        <div className="ml-auto flex gap-1">
          {LENSES.map((lens) => (
            <button
              key={lens}
              onClick={() => void inspect(lens)}
              disabled={busy !== null}
              title={`check this still through the ${lens} lens`}
              className="rounded-lg px-1.5 py-0.5 text-[10px] text-zinc-400 transition-colors
                hover:bg-hover hover:text-zinc-700 disabled:opacity-40"
            >
              {busy === lens ? "…" : lens}
            </button>
          ))}
        </div>
      </div>
      {!latest.size ? (
        <p className="text-[10px] text-zinc-400">No checker verdicts yet — run a lens above.</p>
      ) : null}
      {LENSES.filter((lens) => latest.has(lens)).map((lens) => {
        const turn = latest.get(lens)!;
        const passed = turn.verdict === "pass";
        return (
          <div key={lens} className="flex gap-2">
            <span className={`w-3 shrink-0 text-[11px] ${passed ? "text-live" : "text-stale"}`}>
              {passed ? "✓" : "⚠"}
            </span>
            <span className="w-14 shrink-0 text-[10px] text-zinc-400">{lens}</span>
            <span
              className={`min-w-0 flex-1 text-[10px] leading-relaxed ${
                passed ? "text-zinc-400" : "text-zinc-700"
              }`}
            >
              {/* The stored text already reads "lens: problem. Suggested fix: …" — the label is
                  its own column here, so it is stripped rather than shown twice. */}
              {turn.text.replace(new RegExp(`^${lens}:\\s*`), "")}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/** The conversation about this still, never collapsed, plus the prompt it is drawn from. */
// The assets cast, by name. Named rather than "every agent", because a reel's transcript holds
// the script and storyboard stages' reports too and this column is about the stills.
const ASSET_AGENTS = [
  "asset-maker",
  "style-paper-cutout",
  "style-claymation",
  "mise-en-scene",
  "script-writer",
];

function Talk({ beat, board }: { beat: Beat; board: Board }) {
  const studio = useStudio();
  const pictures = stillPictures(beat, board.staging ?? []);
  // The @-menu resolves against `stillPictures`, not `videoPictures`: the same picture is a
  // different number in the two prompts, and this field feeds the still.
  const prompt = useDraft(beat.asset_prompt, (next) =>
    void studio.guard(() => api.patchBeat(board.slug, beat.n, { asset_prompt: next })),
  );
  return (
    <div className="space-y-3">
      {/* What the crew's agents said about this stage, above the per-still conversation.
          This is the one stage with no `ChatPanel` -- deliberately, because the board agent
          cannot see a picture and `stills.converse` can, and two conversations about one still
          with one of them blind is worse than one. But an agent's REPORT is not a conversation
          about the still; it is what the asset-maker handed back before the checkers looked, and
          without it this page is the only place in the studio where work happens invisibly. */}
      <AgentTurns names={ASSET_AGENTS} />

      <StillChat beat={beat} expanded />

      <div className="space-y-1">
        <span className="text-[10px] uppercase tracking-wide text-zinc-500">the prompt</span>
        <PromptField
          value={prompt.draft}
          onChange={prompt.change}
          onBlur={prompt.flush}
          options={pictures}
          className={`${inputClass} thin h-56 leading-relaxed`}
          placeholder="the layered still description — foreground, subject, background, light"
        />
        <p className="text-[10px] leading-snug text-zinc-400">
          Editing this marks nothing stale: the still it produces is what the render is
          fingerprinted on. Type @ to name a picture above.
        </p>
      </div>
    </div>
  );
}

/** Two reviewer rewrites in a row — the signal that the bible, not the prompt, is wrong. */
function twiceRewritten(turns: AssetTurn[]): boolean {
  const verdicts = turns.filter((turn) => turn.verdict).slice(-2);
  return verdicts.length === 2 && verdicts.every((turn) => turn.verdict === "rewritten");
}
