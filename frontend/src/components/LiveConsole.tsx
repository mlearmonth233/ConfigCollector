import { memo, useEffect, useRef } from "react";

/** Auto-scrolling transcript view - as `output` grows (e.g. via polling a
 * running job), the box stays scrolled to the bottom so new lines are
 * visible without the viewer needing to scroll manually. Memoized since a
 * job with several consoles expanded re-renders its parent every poll tick
 * even for devices whose output hasn't changed since the last one - `output`
 * is a plain string, so this skips the re-render (and the scroll effect)
 * whenever its value is unchanged, regardless of the parent object's
 * identity. */
export const LiveConsole = memo(function LiveConsole({ output }: { output: string }) {
  const ref = useRef<HTMLPreElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [output]);

  return (
    <pre className="config-view live-console" ref={ref}>
      {output || "Waiting for output…"}
    </pre>
  );
});
