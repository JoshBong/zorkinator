import { useEffect, useState } from "react";

/** Presenter Mode: press "P" for large fonts and hidden clutter. */
export function usePresenterMode() {
  const [presenter, setPresenter] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && /input|textarea|select/i.test(target.tagName)) return;
      if (e.key.toLowerCase() === "p") setPresenter((v) => !v);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle("presenter", presenter);
  }, [presenter]);

  return { presenter, setPresenter };
}
