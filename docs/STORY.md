# Zorkinator: a harness that learns how the world works

## The problem nobody's harness solves

Benchmarks like ARC-AGI exist because of one uncomfortable fact: models that ace exams fall apart on
problems they have never seen and were never given the answer to. ARC puzzles are trivial for a
person and brutal for a model, because there is no memorized solution to pattern-match and no
reward signal to climb. You have to look at what happens, form a theory of how the thing works, and
test it.

Text adventures are the same problem in a bigger world. In *Playing With AI* (arXiv 2602.15867),
frontier models played Zork I and completed under 10% of it on average. Not because the commands
are hard, but because nothing they did taught them anything. The paper's own words: they "failed
to learn from previous attempts despite access to conversation history." Given every previous game
to read, they read none of it. Give a model the transcript of its own death and it walks into the
same room and dies again.

That is the gap: models can't turn experience into knowledge. Not in a game, not in a codebase, not
in a task that runs for a thousand steps.

## What a "self-improving harness" usually means

Most harnesses that improve themselves do one thing: they optimize toward a score. Run the agent
fifty times, split the runs into winners and losers, ask a model what the winners did differently,
rewrite the prompt. It is reinforcement learning with a language model as the update rule.

It works when there is a clean reward and a short horizon. It breaks on exactly the problems that
matter:

- **There is no reward until you've already won.** In Zork, in a research problem, in a long
  engineering task, the score comes at the end and tells you nothing about which of your 400
  decisions mattered.
- **It learns the answer, not the world.** A prompt tuned on one map, one seed, one task is a
  walkthrough in disguise. Change the seed and it's back to zero.
- **Nothing it learns is checked.** The rewritten prompt is an opinion. If the model concludes
  "always attack the troll," nothing stops that from becoming policy.

## What Zorkinator does instead

Zorkinator does not optimize a score. It builds a model of how the world works from what actually
happens, and it is only allowed to act on that model to the extent the evidence supports it.

**It learns cause and effect, not answers.** Every move is an experiment: the agent says what it
expects to happen, does the thing, and sees what happened. "Went down without a light, was eaten by
a grue." "Attacked the troll bare-handed, died." "Opened the window, got into the kitchen." Those
are facts about the world, and they are true on any seed. The score is measured, never trained on.

**It tells the model what the game is, not how to win it.** The harness never hands the model a
route. It hands it a world: the rooms it has seen and how they connect, the objects it has found,
what it has tried in this room and what happened, what it is carrying, and what it was trying to do.
The model still has to play. It just isn't playing with amnesia.

**Every belief cites its evidence.** When the agent reflects after a game and proposes a memory ("the
trap door is under the rug," "the lantern battery runs down"), the memory carries the exact move
that produced it. A belief with no evidence does not get stored. A belief the next game contradicts
gets revised or retired. Knowledge is versioned: game N plays with an exact, immutable snapshot of
what was known, so you can diff game 1 against game 10 and see what it learned.

**Authority has to be earned.** A learned rule starts as a caution: the agent is warned, and may
proceed. The rule can block an action only after a mechanical replay of the logged games proves two
things: it would have prevented a real death, and it would never have blocked a move that led to
progress. No model is involved in that decision, and the harness cannot edit its own verifier. The
model proposes; the evidence decides.

**It measures its own understanding.** Because the agent predicts each outcome, we can count how
often it is wrong. A falling surprise rate means the world model is getting better, whether or not
the score has moved yet. That is a learning signal you cannot get from a reward.

**It runs on what a human sees.** Game text and the status line. No save and restore, no
walkthrough, no peeking at the engine's list of valid actions. If it learns, it learns the way a
player does.

## How it grows

```
game 1     cold start: no map, no memories, no rules
   |       play -> every move logged with prediction, outcome, surprise
   v
reflect    deaths and surprises first: propose memories, revise contradicted ones, propose rules
   |       every proposal cites a move; anything uncited is rejected
   v
version 2  an exact manifest: these memories, these rules, nothing else
   |       rules are cautions until replay proves them
   v
game 2     plays version 2; the scribe confirms or contradicts what was carried over
   ...
game N     a map it built, rules it earned, a picture of the world it can check against reality
```

The harness that plays game 10 is not the harness that played game 1. It has the same code and the
same model. What changed is everything it knows, and every piece of that is traceable to something
that happened.

## Why this is the harness problem

Statement One asks for a harness that evolves its own rules, context policies, and guardrails.
Zorkinator's rules are learned, its context is built from what it has learned, and its guardrails are
promoted only by evidence. The part that never evolves is the part that keeps it honest: the
verifier, the promotion procedure, the scoring, the budget.

Zork is the testbed because it is cheap, deterministic, and has a published baseline. The mechanism
is not about Zork. Any long task where the answer isn't known in advance, where feedback comes late,
and where a wrong belief is expensive is the same problem: learn how the thing works from what
happens, keep only what the evidence supports, and never let an unproven belief steer.

## The three-minute demo

1. **The bare loop.** A frontier model, the paper's exact prompt, full chat history. It wanders,
   repeats itself, dies to the same troll. Score by game across a chain: flat. Given its past games to
   read, it looked at them six times in a hundred games.
2. **One harness move.** The prompt the model actually sees: where it is, the exits it knows and the
   ones it hasn't tried, what it carries, what it tried here and what happened, its goal, its
   prediction. Then the outcome, and whether it was surprised.
3. **Between games.** The reflection: the proposed memories with their cited moves, a rule proposal, the
   version manifest that the next game will play. A rule that failed the replay check and stayed a
   caution. One that passed and became a guardrail.
4. **Game N.** The same room where game 1 died. The map it built. The caution firing. The command the
   guardrail blocked, and the move it made instead.
5. **The chart.** Score and surprise rate by game, harness against the bare loop, same model, same
   seed.
