# SOP platform release lzpsop20260821

This branch contains the SOP web platform, Windows launcher source, operating manuals, CVAT integration, real training/validation workflow, and the GitHub-safe application bundle:

- `SOP平台_网页与APP完整软件包_lzpsop20260821.zip`

The 2026-08-21 annotation update adds:

- fixed-station annotation scope and material-class guidance;
- video playback, frame stepping, configurable jumps, timeline seeking, and direct frame input;
- explicit start/end keyframe interpolation with visible track IDs and generated frame lists;
- audited deletion for saved manual boxes and complete tracks;
- browser media cache recovery after login and updated Chinese operating manuals.

The public bundle intentionally excludes passwords, API tokens, runtime databases, camera recordings, large model weights, and production annotation data. Those restricted or oversized artifacts are delivered to the authorized Windows host under:

`D:\lzpsop20260821\SOP平台_完整交付包`

Verify every transferred file with `SHA256SUMS.txt` before installation. Automatic candidate annotations remain pending human review and must not be treated as production ground truth.
