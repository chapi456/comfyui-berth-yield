# Measures

Machine: RTX 4070 (12 GB), 80 GB RAM, Windows, ComfyUI 0.45.3, Berth 0.6.1 (`keep_comfyui_up = true`,
`[park] floor_gib = 8`), a ~35B mixture-of-experts LLM served through Berth (CPU experts, so a cold load takes a lot of
RAM), comfyui-ollama as the LLM node. Workflows submitted as API prompts, exactly as the graphs in `workflows/` export
them.

## 1. Full cycle with the LLM warm in RAM (render, yield, vision LLM, resume, render)

98 s end to end. `berth why comfyui` for that run:

```
asked for the card: foreground
waited: next in line: draining strata [foreground, active]
waited: next in line: evicting parked strata [foreground, parked]
granted, 200 MiB: waited 4.913s
asked to give the card back: strata (foreground) needs the card     <- Berth Yield
parked in RAM, 7120 MiB: residual 240 MiB                           <- ComfyUI out of the card in about 2 s
...
granted, 200 MiB: waited 350ms                                      <- Berth Resume: card back in 0.35 s
resumed from RAM: resumed in 0.0 s
```

The second image follows the prompt the LLM wrote from the first one.

## 2. VRAM at the yield (nvidia-smi, 1 sample per second, run with a cold LLM)

| t (s) | 354 | 356 | 358 | 359 | 361 | 363 | ... | 404 | 411 | 413 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MiB used | 7404 | 7400 | 9786 | 378 | 138 | 138 | 138 | 2252 | 7520 | 8493 |

The render's peak (9786 MiB) is gone within 3 s of Berth Yield; the card stays at 138 MiB while Berth starts the LLM,
which then loads (2252, 7520, 8493 MiB).

## 3. Cold LLM: ComfyUI evicted, job lost

4 runs out of 4 (two workflows) on the same machine, the LLM not in RAM:

```
lease.draining     comfyui  strata needs the card
lease.parked       comfyui  1350 MiB  residual 230 MiB
lease.granted      strata   137 MiB   beside parked comfyui (230 MiB)
lease.park_evicted comfyui  1350 MiB  available RAM is under the floor      (about 60 s later)
...
lease.requested    comfyui                                                   <- Berth Resume: nothing to resume
```

ComfyUI is stopped by the eviction, so the prompt is not in its queue or history when the resume comes (the submitting
client sees "prompt lost"). A warm-up LLM call just before the job does not change it: when ComfyUI asks for the card at
the start of the job, Berth drains the LLM and cannot park it (`park refused: needs 8.3 GiB, 0.0 GiB available above
the floor`), so the LLM is cold again at the yield.

## 3. Same cycles with `[park] floor_gib = 3`

Same machine, Berth restarted with the lower floor. 12 runs through the job API (6 of the simple graph, 6 of the vision
graph), the LLM started cold by Berth each time (it is parked in RAM only while ComfyUI runs):

- 10 runs done, 89 to 259 s; no `park_evicted` for ComfyUI; ComfyUI resumed from RAM ("resumed in 0.0 s").
- 1 run lost: ComfyUI asked for the card 150 ms after the LLM's grant, while the LLM was still loading; Berth's unload
  of it answered `409 Conflict`, the LLM lease was released, ComfyUI restarted (new pid) and the prompt was gone.
- 1 run refused before it started: `503 busy, "switch in progress"` from the submitting side.
