# RUNECLAW 3D mascot models

`js/mascot3d.js` (three.js vendored under `vendor/three/`) loads the model its
`MODEL_URL` names and mounts it on `/agent`, in the landing hero (above 900px
only) and as the dashboard's reactive avatar.

## The owl (live since 2026-10-19)

`runeclaw_owl_round.glb` — the round RUNECLAW owl: navy body, steel band with
rivets, hexagonal badge with the rune, glowing eyes and rune (emissive #3FB6FF,
`KHR_materials_emissive_strength`, strength 3 on the rune and 1.2 on the eyes,
under the hero's glow ceiling of 5). Built procedurally in Blender 4.2; about
2 m across, Y-up, facing +Z. 660 KB, uncompressed geometry (the Draco cut needs
a decoder the site does not vendor; the USDZ is for AR Quick Look and is not
served).

Nodes: `RUNECLAW_Owl` > `Body`, `HornL`, `HornR`, `Band`, `Rivet00..13`,
`Badge`, `Rune`, `EyeL` > `PupilL`, `EyeR` > `PupilR`, `Beak`.

Animation clips, as the loader reads them (names lower-cased):

| clip | what | the loader's use |
|---|---|---|
| `Idle` | a 6 cm bob, 3 s loop | the resting expression |
| `Blink` | eyes close, 0.33 s | `alert` |
| `Talk` | squash bounce and beak flap, 0.7 s | `analyze` (held while the chat thinks) and `execute` |

The loader keeps its reactive names (`analyze` / `alert` / `execute`); a model
that lacks a clip of that name plays the alias above, so the dashboard's
reactions work on the owl without renaming the clips in the source file.

## The earlier agent (alternates, still committed)

`RUNECLAW_Command_Core_Mascot{,_Premium,_Hero,_Luminous}.glb` and `agent.glb`
(a copy of Luminous) are the Viking agent the hero showed before the owl.
`mascot_assets.test.js` pins the glow budget and the size of whatever
`MODEL_URL` names, and proves the ceiling against Luminous while it is here.

## Model requirements (any replacement)

- glTF-binary, single file, uncompressed geometry, embedded PBR textures.
- Y-up; the viewer scales to a fixed height and centres the model itself.
- An `Idle` (or first) clip loops; without clips the viewer adds a soft bob.
- Under 1 MB and under the glow ceiling, or `mascot_assets.test.js` says why not.
