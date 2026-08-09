"""Paper-cutout Reels: concept -> script -> stills -> chained clips -> 1080x1920 MP4.

Stages are separable on purpose. Everything up to the video goes through Google's API --
the script and the board edits from Gemini (`gemini.py`), the opening stills from Papercut
Studio next door (`papercut.py`, `stills.py`) -- and only rendering touches a GPU. Words are
cents, stills are cents, a reel is dollars: iterate cheaply, then pay once.

    from paperreel import config, planner, pipeline

    board = planner.plan("a paper pig finds a pond", beats=4, seconds=10)
    result = pipeline.render_reel(board, workdir, seconds=10)
    print(result.reel, result.cost)
"""

from . import comfy, config, gemini, media, pipeline, planner, script, stills

__all__ = ["comfy", "config", "gemini", "media", "pipeline", "planner", "script", "stills"]
