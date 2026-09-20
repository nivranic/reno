/** Safe Markdown rendering: react-markdown never renders raw HTML by
 * default; remote images are not auto-loaded (privacy + offline reading).
 * Internal /videos/... links route through React Router. Headings get
 * text-based ids so in-page TOC anchors work without extra plugins. */
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Link } from "react-router";
import { cn } from "@/lib/cn";

function nodeText(node: React.ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (typeof node === "object" && "props" in node) {
    return nodeText((node as { props: { children?: React.ReactNode } }).props?.children);
  }
  return "";
}

const HEADING_IDS = ["h1", "h2", "h3", "h4", "h5", "h6"] as const;
type HeadingTag = (typeof HEADING_IDS)[number];

export function MarkdownView({ md, className }: { md: string; className?: string }) {
  const headingComponents = Object.fromEntries(
    HEADING_IDS.map((tag) => [
      tag,
      ({ children }: { children?: React.ReactNode }) => {
        const H = tag as HeadingTag;
        return <H id={nodeText(children)}>{children}</H>;
      },
    ]),
  ) as Partial<Record<HeadingTag, (p: { children?: React.ReactNode }) => React.JSX.Element>>;

  return (
    <div className={cn("reno-md", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          ...headingComponents,
          a({ href, children }) {
            const url = href ?? "";
            if (url.startsWith("/")) {
              const to = url as string;
              return (
                <Link to={to} className="reno-md-link">
                  {children}
                </Link>
              );
            }
            return (
              <a href={url} target="_blank" rel="noreferrer noopener" className="reno-md-link">
                {children}
              </a>
            );
          },
          img({ alt }) {
            // remote images are not loaded; show placeholder instead (offline + privacy)
            return <span className="reno-md-img-off">[图片未加载:{alt ?? ""}]</span>;
          },
        }}
      >
        {md}
      </ReactMarkdown>
    </div>
  );
}

/** Extract (level, text) headings for a report TOC. Text doubles as the
 * in-page anchor id (see MarkdownView heading components). */
export interface TocEntry {
  level: number;
  text: string;
  id: string;
}

export function extractToc(md: string): TocEntry[] {
  const out: TocEntry[] = [];
  let inFence = false;
  md.split("\n").forEach((line) => {
    if (/^\s*```/.test(line)) inFence = !inFence;
    if (inFence) return;
    const m = /^(#{1,4})\s+(.*)$/.exec(line.trim());
    if (m) {
      const text = m[2].trim();
      out.push({ level: m[1].length, text, id: text });
    }
  });
  return out;
}
