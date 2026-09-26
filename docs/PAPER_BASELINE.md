# Paper baseline — arXiv 2602.15867 (Gerrits, "Playing With AI")

## Basic prompt (verbatim — paper mode uses exactly this; harness mode = this + state blocks)

> I'm going to play the text adventure game Zork with you. I'll paste the game's output after each move, and you should respond with ONLY the command to enter into the game—nothing else, do not include any explanation in the final command.

Both of the paper's prompts told the model not to use `restart` and allowed an "I give up" reply when stuck.
The advanced prompt (Zork manual guidance) gave no improvement, so we use the basic one.

## Setup in the paper
- Models: Claude Opus 4.5, Claude Sonnet 4.5, GPT-5.2, Gemini 3. Thinking modes gave no gain.
- 500-move cap. 5 runs per model x prompt (40 total). Full chat history within a run.
- Custom Python script driving a **browser** Zork, not Jericho. SAVE/RESTORE not stated.
- Result: ~10% of 350 on average; best Opus 4.5 ~75/350.

## Consequence
Their game build differs from ours, so we rerun paper mode ourselves on Jericho (same prompt, same model,
full history, 500-move cap). Compare harness vs our paper-mode run; cite 75 as the published reference.
Treat "I give up" as end of game (end_reason "gave_up").
