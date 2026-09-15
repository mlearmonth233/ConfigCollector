import { useEffect, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import { useLocation } from "react-router-dom";
import rehypeSlug from "rehype-slug";
import remarkGfm from "remark-gfm";

// The same file that's published in the repo (docs/USER_GUIDE.md) - imported
// as text at build time, so the guide in the app can never drift from the
// one on GitHub. See vite.config.ts for the dev-server allowance.
import guideMarkdown from "../../../docs/USER_GUIDE.md?raw";

interface TocEntry {
  id: string;
  title: string;
}

/** GitHub-style heading id: lowercase, punctuation dropped, spaces to dashes -
 *  matches what rehype-slug generates, so sidebar links land on headings. */
function slugify(title: string): string {
  return title
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-");
}

/** The markdown's own "Contents" list is redundant next to the sidebar, so
 *  it's dropped from the rendered body (everything between the **Contents**
 *  line and the first horizontal rule). */
function stripContents(md: string): string {
  const start = md.indexOf("**Contents**");
  if (start === -1) return md;
  const end = md.indexOf("\n---", start);
  return end === -1 ? md : md.slice(0, start) + md.slice(end + 4);
}

export function Help() {
  const location = useLocation();
  const body = useMemo(() => stripContents(guideMarkdown), []);
  const toc = useMemo<TocEntry[]>(
    () =>
      body
        .split("\n")
        .filter((line) => line.startsWith("## "))
        .map((line) => {
          const title = line.slice(3).trim();
          return { id: slugify(title), title };
        }),
    [body]
  );

  // Deep links (/help#8-schedules) and sidebar clicks scroll to the heading
  // once it exists - the browser's own jump fires before the markdown has
  // rendered, so it's done here after render instead.
  useEffect(() => {
    if (!location.hash) return;
    const el = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (el) el.scrollIntoView({ block: "start" });
  }, [location.hash, body]);

  return (
    <div className="page page-wide help-page">
      <aside className="help-toc" aria-label="Guide sections">
        <p className="help-toc-title">User guide</p>
        <ol>
          {toc.map((entry) => (
            <li key={entry.id}>
              <a href={`#${entry.id}`}>{entry.title.replace(/^\d+\.\s*/, "")}</a>
            </li>
          ))}
        </ol>
        <p className="field-hint help-toc-foot">
          Also on GitHub as{" "}
          <a
            href="https://github.com/mlearmonth233/ConfigCollector/blob/main/docs/USER_GUIDE.md"
            target="_blank"
            rel="noreferrer"
          >
            docs/USER_GUIDE.md
          </a>
          .
        </p>
      </aside>
      <article className="doc">
        <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSlug]}>
          {body}
        </ReactMarkdown>
      </article>
    </div>
  );
}
