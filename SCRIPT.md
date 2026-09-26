# Zorkinator: 1-minute demo video script

The results play on screen, and this narration runs over them. It explains how the loop works and how
knowledge is stored and passed between games, so viewers can read the numbers themselves. About 160 spoken
words, which is 60 seconds at a normal pace. The "On screen" lines are suggestions; match them to the footage
you capture.

---

## 0:00–0:10 · Zork, and the problem

**On screen:** the Zork I opening ("West of House…"), then the baseline results.

> Zork is a classic text adventure: you explore a world, solve puzzles, and try not to die, all by typing commands. 
> language models don't learn from experience. Give one a long, unfamiliar game like Zork and its past attempts,
>  and it still makes the same mistakes game after game. Memory alone isn't learning.

## 0:10–0:25 · The inner loop: one game

**On screen:** `docs/img/harness.png`, with the inner loop highlighted; then a live game.

> Zorkinator plays with only what a human can see. Every move, it rebuilds a small prompt from its own working knowledge:
> the map, items, what it already tried in this room, and cautions it has learned. It predicts what will happen, 
> and every move is logged as evidence.

## 0:25–0:45 · The outer loop: between games

**On screen:** the diagram with the outer loop and Atlas highlighted; then the memory and version view.

> After each game, fixed code carries the map and the outcome of each action forward, and a reflector turns
> deaths and surprises into memories. Each memory cites the move that proves it. The whole change set is
> published to MongoDB Atlas as a new, fixed version, and the next game loads exactly that version.

## 0:45–0:55 · Earned authority

**On screen:** the rulebook, with a caution becoming a hard rule; then game 1 and game N side by side.

> At every step, what it learned shows up in the prompt: notes for the room it's in and cautions for risky actions.
>  Those only inform the choice. A rule can actually block a command only after replaying past games
> proves it would have prevented a death.

## 0:55–1:00 · Close

**On screen:** the Zorkinator title and the repo link.

> Not a better prompt. A harness that earns what it knows.
