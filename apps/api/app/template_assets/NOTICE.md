# Editable visual assets

The six HTML components and their editable binding metadata in `openfilm/`
are copied without modification from OpenFilm commit
`758b1a3888819bf191db191f690c4ca2fcb292af`. They are CC0-1.0; see
`openfilm/LICENSE.md` and the exact files/digests in `openfilm/provenance.json`.
Yaohe supplies its own task/version binding, document host and media adapter.

`openfilm-clock.json` contains FILM_CLOCK from that commit's
`packages/openfilm/src/host.mjs`, under MIT, copyright 2026 OpenFilm
contributors. Its notice is bundled in desktop `assets/licenses/OpenFilm-MIT.txt`.
Preview and export share this clock and the document compiler.

Commercial ChatCut's Apache-2.0 public agent plugin does not establish a
redistribution license for the installed application's template assets.
Independent OpenChatCut uses AGPL-3.0 for its editor and describes its official
community resources as MIT, while contributor packages choose their own
licenses. Its bundled template JSON lacks per-item creator/license metadata;
no ChatCut or OpenChatCut template code is included here. A separately licensed
resource can be added after its own source, dependency and license are pinned.
