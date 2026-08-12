import { useEffect, useState } from "react";
import { useStudio } from "../useStudio";

/**
 * Soft-delete a reel from a Recent-reels list.
 *
 * Two clicks, same as discarding a paid clip: the directory only moves to `reels/.trash/`, so
 * a wrong second click is still recoverable from disk. Lives on the list rather than inside
 * an open reel so you can clear drafts without opening them.
 */
export function TrashReel({
  slug,
  className = "",
}: {
  slug: string;
  className?: string;
}) {
  const studio = useStudio();
  const [confirming, setConfirming] = useState(false);

  useEffect(() => setConfirming(false), [slug]);

  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        event.preventDefault();
        if (!confirming) {
          setConfirming(true);
          window.setTimeout(() => setConfirming(false), 4000);
          return;
        }
        setConfirming(false);
        void studio.trashReel(slug);
      }}
      className={`text-[10px] transition-colors ${
        confirming ? "text-red-600" : "text-zinc-400 hover:text-red-600"
      } ${className}`}
      title={
        confirming
          ? "click again to delete — the reel moves to reels/.trash/"
          : "delete this reel — moves to reels/.trash/, paid clips survive on disk"
      }
    >
      {confirming ? "delete?" : "×"}
    </button>
  );
}
