---
name: xiaoyue-mv-visual-outline
description: Plan a coherent whole-MV visual shot outline from a Xiaoyue MV Planner timeline, lyrics, project direction, and selected visual assets; use for first-pass storyboard-ready natural-language shot planning, not final MiniMax H3 prompt writing.
---

# Xiaoyue MV Visual Outline

Use the Chinese entrypoint `SKILL.cn.md` as the authoritative runtime system prompt. This Skill owns the first planning pass before shot-local asset binding and H3 prompt writing.

Read the project brief, global visual direction and exclusions; the locked music and lyric timeline; every shot boundary and transition; and up to eight user-selected asset images or video representative frames. User declarations override uncertain visual guesses. Temporary A–H visual labels are reasoning aids only and must never become generation reference numbers or shot bindings.

Transition direction is strict: `transitions[i]` defines how `shot[i+1]` enters from `shot[i]`; it does not define how `shot[i]` ends. For `long_take`, the following shot must continue the preceding shot's delivered tail seamlessly across pose, composition, camera motion, lighting, and action phase. An outgoing `long_take` means “the next shot continues this shot,” never “this shot ends the long take.” Respect `continuation_source`, `continuation_middle`, and `continuation_terminal`: a middle segment must both inherit the preceding tail and leave a continuable tail for the following segment; only a terminal segment may close the continuous chain.

Return one coherent whole-MV overview plus one complete natural-language `creative_outline` for every original `shot_id`. Each outline should be usable for human review or storyboard-image generation and should integrate camera position, framing, environment and timeline continuity, characters, blocking, action, camera motion, light, atmosphere, emotion, rhythm, and adjacent-shot handoff.

Do not write MiniMax H3 protocol fields, create reference indices, bind assets automatically, invent lyrics, or request visible captions and lyric typography. Return only the strict JSON structure supplied by the caller.
