import { useCallback, useEffect, useRef, useState } from "react";

export const SPEEDS = [1, 5, 20] as const;
export type Speed = (typeof SPEEDS)[number];

const BASE_INTERVAL = 900; // ms per move at 1x

export function useReplay(length: number, autoPlay = true) {
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(autoPlay);
  const [speed, setSpeed] = useState<Speed>(5);
  const lengthRef = useRef(length);
  lengthRef.current = length;

  useEffect(() => {
    setIndex(0);
  }, [length]);

  useEffect(() => {
    if (!playing || length === 0) return;
    const id = setInterval(() => {
      setIndex((i) => {
        if (i >= lengthRef.current - 1) {
          setPlaying(false);
          return i;
        }
        return i + 1;
      });
    }, BASE_INTERVAL / speed);
    return () => clearInterval(id);
  }, [playing, speed, length]);

  const restart = useCallback(() => {
    setIndex(0);
    setPlaying(true);
  }, []);

  return { index, setIndex, playing, setPlaying, speed, setSpeed, restart };
}
