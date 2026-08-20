# SOP platform release lzpsop20260821

This branch contains the SOP web platform, Windows launcher source, operating manuals, CVAT integration, real training/validation workflow, and the GitHub-safe application bundle:

- `SOP平台_网页与APP完整软件包_lzpsop20260821.zip`

The public bundle intentionally excludes passwords, API tokens, runtime databases, camera recordings, large model weights, and production annotation data. Those restricted or oversized artifacts are delivered to the authorized Windows host under:

`D:\lzpsop20260821\SOP平台_完整交付包`

Verify every transferred file with `SHA256SUMS.txt` before installation. Automatic candidate annotations remain pending human review and must not be treated as production ground truth.
