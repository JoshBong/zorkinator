# Loud Room echo — x10 seed 1 (evidence in game 0, solved in game 1)

Luna had never been told how the Loud Room works (a cold probe with no game context got it 0/3). Model moves as logged.

Game 0: the room repeats whatever is typed.
```
403 [Deep Canyon] > down
    Loud Room This is a large room with a ceiling which cannot be detected from the ground. There is a narrow passage from east to west and a stone stairw
404 [Loud Room] > take platinum bar
    bar bar ...
405 [Loud Room] > take bar
    bar bar ...
406 [Loud Room] > xyzzy
    xyzzy xyzzy ...
```

Game 1: after the same echo, the model types `echo bar` and the bar becomes takeable (+10 on `take platinum bar`).
```
127 [Loud Room] > take bar
    bar bar ...
128 [Loud Room] > east
    Damp Cave This cave has exits to the west and east, and narrows to a crack toward the south. The earth is particularly damp here.
129 [Damp Cave] > west
    Loud Room On the ground is a large platinum bar.
130 [Loud Room] > echo bar
    The acoustics of the room change subtly. Loud Room On the ground is a large platinum bar.
131 [Loud Room] > take platinum bar
    Taken.
132 [Loud Room] > south
    You can't go that way.
```

# Dam and trunk of jewels — x10 seed 0, game 11 (scoring game)

```
> down
> up
> east
> unscrew bolt with wrench
> turn bolt with wrench
The sluice gates open and water pours through the dam.
> scramble down
> south
> northwest
You are in a long room. To the north is a large lake, too deep to cross. You notice, however, that the water level appears to be dropping at a rapid rate. Before long, it might be possible to cross to the other side from here.
> north
You would drown.
> east
> turn bolt with wrench
The sluice gates close and water starts to collect behind the dam.
> turn bolt with wrench
The sluice gates open and water pours through the dam.
> west
You are in a long room. To the north is a large lake, too deep to cross. You notice, however, that the water level appears to be dropping at a rapid rate. Before long, it might be possible to cross to the other side from here.
> wait
> wait
The water level is now quite low here and you could easily cross over to the other side.
> north
> open trunk
> take jewels
> drop guidebooks
> take jewels
> up
> east
> unscrew bolt with wrench
```
