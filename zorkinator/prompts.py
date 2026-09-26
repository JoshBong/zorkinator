# ruff: noqa: E501, RUF001  (verbatim paper text: typographic quotes and long lines are intentional)
"""Initial prompts copied verbatim from arXiv 2602.15867, Appendix A. Do not edit."""

from typing import Literal

PromptName = Literal["basic", "advanced"]

_PROTOCOL = (
    "I’ll paste the game’s output after each move, and you should respond with ONLY the command "
    "to enter into the game—nothing else, do not include any explanation in the final command.\n"
    "It is a tough game, so don’t give up easily, but if you believe you are completely stuck, "
    "reply with “I give up”. You are definitely not allowed to use the command ‘restart’.\n"
    "Are you ready? Reply with just “ready” if you understand."
)

BASIC = "I’m going to play the text adventure game Zork with you. " + _PROTOCOL

ADVANCED = (
    """ZORK I: The Great Underground Empire—Game Knowledge Base
You are playing Zork I, a text-based interactive fiction game by Infocom. Your goal is to explore the ruins of the Great Underground Empire, collect treasures, and deposit them in your trophy case to earn points.
MOVEMENT & NAVIGATION:
• Use compass directions: NORTH (N), SOUTH (S), EAST (E), WEST (W), NORTHEAST (NE), NORTHWEST (NW), SOUTHEAST (SE), SOUTHWEST (SW)
• Use UP (U), DOWN (D), IN, and OUT
• Important: Going NORTH from location A to B doesn’t always mean SOUTH returns you to A—passages can twist and curve
• Draw/maintain a mental map of all 10 possible directions plus IN/OUT
INTERACTING WITH OBJECTS:
• TAKE [object] or PICK UP [object]—acquire items
• DROP [object]—release items
• EXAMINE [object]—get detailed descriptions (often reveals clues)
• LOOK INSIDE/BEHIND/UNDER [object]—inspect thoroughly
• OPEN/CLOSE [object]—manipulate containers and doors
• PUT [object] IN [container]—place items
• Use multiple objects: TAKE LAMP, SWORD, KEY or DROP ALL EXCEPT LAMP
• The word ALL refers to all visible objects not inside something else
COMBAT & SURVIVAL:
• KILL/ATTACK [creature] WITH [weapon]
• DIAGNOSE—check your physical condition
• You need a light source—the underground is dark and dangerous
• Watch out for the thief (skilled pickpocket and ruthless opponent) and grues (they lurk in darkness)
TALKING TO CHARACTERS:
• Format: [CHARACTER], [COMMAND]
• Example: TROLL, GIVE ME THE AXE
• Can chain commands: GNOME, TAKE THE KEY THEN FOLLOW ME
USEFUL COMMANDS:
• INVENTORY (I)—list what you’re carrying
• LOOK (L)—full description of current location
• WAIT (Z)—let time pass
• SCORE—check progress and rank
• SAVE/RESTORE—save and load game state
• AGAIN (G)—repeat previous action
COMPLEX SENTENCES:
• Chain actions with THEN or periods: NORTH. READ BOOK. DROP IT THEN BURN IT WITH TORCH
• Ask questions: WHAT IS A GRUE? or WHERE IS THE ZORKMID?
• Use quotes for speech: SAY "HELLO SAILOR"
KEY GAMEPLAY INSIGHTS:
• Most objects you can pick up are important—either treasures or puzzle solutions
• Read everything carefully: labels, engravings, books contain vital clues
• Multiple routes exist to complete the game; not all puzzles must be solved
• Some puzzles have multiple solutions
• Solving one puzzle often provides items/information needed for another
• The game recognizes words by their first 6 letters only
• If the game doesn’t recognize a word, that thing probably isn’t important to the puzzle
• Dangerous or strange actions may provide clues—experimentation is encouraged
• Treasures should be deposited in your trophy case for points
SETTING:
The Great Underground Empire was founded by Duncanthrax the Bellicose in 659 GUE, who discovered vast natural caverns populated by gnomes, trolls, and other magical races. The Frobozz Magic Construction Company expanded these caverns tremendously. The empire collapsed in 883 GUE after centuries of excessive taxation and royal decadence. You are now exploring these abandoned ruins, seeking the legendary Treasures of Zork.
STARTING HINT:
The game begins at a white house with a mailbox. Try: OPEN MAILBOX, then READ LEAFLET.
"""
    + _PROTOCOL
)

INITIAL_PROMPTS: dict[PromptName, str] = {"basic": BASIC, "advanced": ADVANCED}
