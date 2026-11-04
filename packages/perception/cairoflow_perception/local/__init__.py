"""Local vehicle detection on the NGSIM camera 2 video, and the harness that scores it.

Nothing here calls a hosted model. `scoring` imports only the standard library and numpy at
module load; OpenCV and the repository's contract-bound packages are imported where they are
used, and torch only inside a detector's own factory, so the harness can run from another venv.
"""
