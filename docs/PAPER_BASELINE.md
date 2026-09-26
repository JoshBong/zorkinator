# Paper baseline — arXiv 2602.15867 (Gerrits, "Playing With AI")

## Prompts

Both of the paper's initial prompts (Appendix A) are in `zorkinator/prompts.py`, verified word for word against the
paper's HTML. Baseline uses `basic`. The advanced prompt gave no improvement in the paper.

## Protocol (implemented in `zorkinator/runner.py`, `play("paper", ...)`)
1. Send the initial prompt; the model replies "ready".
2. Paste the game's opening text; each reply's first line is the command; paste the game output back. Full history.
3. A run ends on: "I give up", death, completing the game, or 500 commands issued.
4. No thinking, default sampling (the paper doesn't specify any settings).
5. Ours only: `save`/`restore`/`restart` are blocked by the adapter; the model is told "[Not allowed: ...]" and it
   counts as a command. The paper's advanced prompt lists SAVE/RESTORE, so expect it to try.

## Setup in the paper
- Models: Claude Opus 4.5, Claude Sonnet 4.5, GPT-5.2, Gemini 3. Thinking modes gave no gain.
- 500-move cap. 5 runs per model x prompt (40 total). Full chat history within a run.
- Custom Python script driving a **browser** Zork, not Jericho. SAVE/RESTORE not stated.
- Result: ~10% of 350 on average; best Opus 4.5 ~75/350.

## Consequence
Their game build differs from ours, so we rerun paper mode ourselves on Jericho (same prompt, same model,
full history, 500-move cap). Compare harness vs our paper-mode run; cite 75 as the published reference.
Jericho reports `done` at the first death (Zork would otherwise reincarnate you), matching the paper's "player dies" end condition.
