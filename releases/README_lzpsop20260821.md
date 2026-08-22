# SOP platform release lzpsop20260821

This branch contains the SOP web platform, Windows launcher source, operating manuals, CVAT integration, real training/validation workflow, and the GitHub-safe application bundle:

- `SOP平台_网页与APP完整软件包_lzpsop20260821.zip`

The 2026-08-21 annotation update adds:

- fixed-station annotation scope and material-class guidance;
- video playback, frame stepping, configurable jumps, timeline seeking, and direct frame input;
- automatic video-content tracking with optional human end-keyframe correction, visible track IDs, and generated frame lists;
- draggable tracked boxes with eight resize handles and overwrite-save support;
- audited deletion for AI candidates, saved manual boxes, individual tracked boxes, and complete tracks;
- exact-frame SQLite queries plus stale-request cancellation for faster frame jumps;
- persistent CVAT ARM64/amd64 binfmt recovery through the local storage guard timer;
- browser media cache recovery after login and updated Chinese operating manuals.

The 2026-08-22 archive update additionally records all distinct local web generations,
deploys them on isolated LAN ports, restores the current integrated version to port 8096,
and adds a version index at port 8099. See `SOP_VERSION_INDEX_20260822.md` and
`deploy/versioned/README.md` in the repository.

The public bundle intentionally excludes passwords, API tokens, runtime databases, camera recordings, large model weights, and production annotation data. Those restricted or oversized artifacts are delivered to the authorized Windows host under:

`D:\lzpsop20260821\SOP平台_完整交付包`

Verify every transferred file with `SHA256SUMS.txt` before installation. Automatic candidate annotations remain pending human review and must not be treated as production ground truth.
