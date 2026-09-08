import { useEffect, useRef } from "react";

/** Auto-scrolling transcript view - as `output` grows (e.g. via polling a
 * running job), the box stays scrolled to the bottom so new lines are
 * visible without the viewer needing to scroll manually. */
export function LiveConsole({ output }: { output: string }) {
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
}
