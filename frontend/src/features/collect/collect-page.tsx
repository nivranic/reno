/** Collection assistant: platform-guided bookmarklet flow.
 * Bookmarklets are generated from the CURRENT origin (works on LAN/iOS,
 * never a hardcoded developer localhost). Config status comes from
 * /api/health — booleans only, secrets are never exposed to the client. */
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BookmarkPlus, Check, Copy, ExternalLink, Info } from "lucide-react";
import { healthQuery } from "@/lib/queries";
import { PageHeader, ErrorState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/shared/toaster";
import { cn } from "@/lib/cn";

function bookmarkletCode(origin: string) {
  return (
    "javascript:(async()=>{const links=[...new Set([...document.querySelectorAll('a[href*=\"/video/\"]')]" +
    ".map(a=>a.href.split('?')[0]))];if(!links.length){alert('reno:本页没有视频链接,请先滚动加载');return;}" +
    `const r=await fetch('${origin}/api/import-batch',{method:'POST',headers:{'Content-Type':'application/json'},` +
    "body:JSON.stringify({urls:links})});const j=await r.json();" +
    "alert('reno:已提交 '+links.length+' 个链接\\n新增'+j.imported+' 重复'+j.duplicate+' 失败'+j.failed);})()"
  );
}

export default function CollectPage() {
  const health = useQuery(healthQuery);
  const toast = useToast();
  const [platform, setPlatform] = useState<"douyin" | "bilibili">("douyin");
  const origin = window.location.origin;
  const bmCode = bookmarkletCode(origin);
  // React refuses to render javascript: URLs into href (security feature);
  // bookmarklets are the legitimate exception — set the attribute directly
  // so dragging the link to the bookmarks bar keeps working.
  const bmRef = useRef<HTMLAnchorElement>(null);
  useEffect(() => {
    bmRef.current?.setAttribute("href", bmCode);
  }, [bmCode]);

  const copyCode = async () => {
    try {
      await navigator.clipboard.writeText(bmCode);
      toast.success("书签代码已复制:新建书签并粘贴为网址即可");
    } catch {
      toast.error("复制失败,请手动复制代码框内容");
    }
  };

  return (
    <div className="mx-auto w-full max-w-[820px] px-4 py-5 md:px-6">
      <PageHeader
        title="采集助手"
        desc="在自己登录的浏览器页面里,把已加载的视频链接提交给本机 reno。不登录、不破解、不模拟客户端。"
      />

      {health.isError ? (
        <div className="mb-4">
          <ErrorState error={health.error} onRetry={() => void health.refetch()} context="服务状态" />
        </div>
      ) : health.data ? (
        <div className="mb-4 flex flex-wrap items-center gap-2 rounded-panel border border-line bg-surface px-4 py-3 text-[13px]">
          <span className="text-muted">服务状态:</span>
          <StatusChip ok label="reno 在线" />
          <StatusChip ok={health.data.cookies_configured} label={health.data.cookies_configured ? "下载 cookie 已配置" : "下载 cookie 未配置(抖音下载需要)"} />
          <StatusChip ok={health.data.vlm_enabled} label={health.data.vlm_enabled ? "视觉理解开启" : "视觉理解关闭(降级模式)"} />
          <span className="ml-auto font-mono text-[11.5px] text-muted">{health.data.atomize_model}</span>
        </div>
      ) : null}

      {/* platform tabs */}
      <div className="mb-4 inline-flex rounded-ctl border border-line bg-surface-2 p-1">
        {(
          [
            ["douyin", "抖音收藏"],
            ["bilibili", "B站收藏夹"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            onClick={() => setPlatform(id)}
            className={cn(
              "cursor-pointer rounded-[6px] px-3.5 py-1.5 text-[13px] font-medium transition-colors",
              platform === id ? "bg-surface text-ink shadow-sm" : "text-muted hover:text-ink-2",
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {platform === "douyin" ? (
        <StepCard
          n={1}
          title="打开抖音「我的收藏」页"
          desc={
            <>
              浏览器登录抖音后打开
              <code className="mx-1 rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[12px]">
                www.douyin.com/user/self?showTab=favorite_collection
              </code>
              ,向下滚动加载出想导入的视频。
            </>
          }
        />
      ) : (
        <StepCard
          n={1}
          title="打开B站收藏夹页(或用整夹自动导入)"
          desc={
            <>
              打开
              <code className="mx-1 rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[12px]">
                space.bilibili.com/&lt;你的ID&gt;/favlist?fid=&lt;夹ID&gt;
              </code>
              并滚动加载;或整夹自动:
              <code className="mx-1 rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[12px]">
                python -m reno import-favlist "收藏夹完整链接"
              </code>
              (私密夹需在 config.local.json 配 ytdlp_cookies)。
            </>
          }
        />
      )}

      <StepCard
        n={2}
        title="安装采集书签(拖到书签栏,或复制代码手动新建)"
        desc={`书签会把当前页面已加载的视频链接批量提交到 ${origin}。地址在局域网其它设备(如 iPhone)上同样以这里显示的地址为准——不要写 localhost。`}
      >
        <div className="mt-3 space-y-2.5">
          <a
            ref={bmRef}
            href="#"
            draggable
            onClick={(e) => e.preventDefault()}
            className="inline-flex cursor-grab items-center gap-2 rounded-ctl bg-[#e11d48] px-4 py-2.5 text-[13.5px] font-semibold text-white shadow-sm hover:brightness-110"
            title="拖动我到浏览器书签栏"
          >
            <BookmarkPlus size={15} /> ➦ 收藏页 → reno
          </a>
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1 overflow-x-auto rounded-ctl border border-line bg-surface-2/60 p-2.5">
              <code className="block whitespace-pre font-mono text-[10.5px] leading-relaxed text-muted">
                {bmCode}
              </code>
            </div>
            <Button variant="outline" size="sm" onClick={() => void copyCode()} className="shrink-0">
              <Copy size={13} /> 复制
            </Button>
          </div>
          <p className="text-[11.5px] text-muted">提示:拖动到书签栏最稳;Safari 可先显示书签栏再拖。</p>
        </div>
      </StepCard>

      <StepCard
        n={3}
        title="在收藏页点书签,提交当前已加载的部分"
        desc="每次点击只提交「当前已加载」的链接,可分批多次;提交后回到收件箱看队列。"
      />

      <StepCard n={4} title="抖音视频下载的前提(一次性)" desc={
        <>
          抖音对无 cookie 的下载请求返回 403(yt-dlp 要求 fresh cookies,
          <b>不要求登录</b>)。在 <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[12px]">config.local.json</code> 加
          <code className="mx-1 rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[12px]">"ytdlp_cookies": "cookies.txt"</code>,
          用浏览器插件(如 Get cookies.txt)从 douyin.com 导出(退出登录状态导出也可以)。
        </>
      } />

      <StepCard
        n={5}
        title="处理节奏"
        desc="单日大批量(几十个视频)会触碰 coding plan 配额;建议分日处理,或在配置中切换 glm-4.5-flash(独立配额池)。每个视频约 1-2 万 token,见 docs/cost-notes.md。"
      />

      <div className="mt-5 rounded-panel border border-line bg-surface-2/40 p-4 text-[12.5px] leading-relaxed text-muted">
        <p className="mb-1.5 flex items-center gap-1.5 font-medium text-ink-2">
          <Info size={14} /> 常见问题
        </p>
        <ul className="list-disc space-y-1 pl-5">
          <li>书签无响应/报网络错误:确认 reno serve 正在运行,且书签里的地址与当前一致(本页自动生成,误手改会失效)。</li>
          <li>跨源被浏览器拦截:书签运行在 douyin/bilibili 页面上,后端已为这两个来源开 CORS 白名单;其它站点不支持。</li>
          <li>iPhone/iPad 使用:确保手机与运行 reno 的电脑在同一局域网,书签地址用本页显示的局域网 IP 形式,而不是 localhost。</li>
          <li>书签采集只拿「链接列表」,真正的下载由 reno 后端完成(抖音需先配好第 4 步的 cookie)。</li>
        </ul>
        <p className="mt-2 flex items-center gap-1">
          <ExternalLink size={12} /> 详细文档见仓库
          <code className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[11.5px]">
            docs/收藏夹批量导入.md
          </code>
        </p>
      </div>
    </div>
  );
}

function StatusChip({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11.5px]",
        ok
          ? "border-st-ok-line bg-st-ok-bg text-st-ok"
          : "border-st-wait-line bg-st-wait-bg text-st-wait",
      )}
    >
      {ok ? <Check size={11} /> : <Info size={11} />}
      {label}
    </span>
  );
}

function StepCard({
  n,
  title,
  desc,
  children,
}: {
  n: number;
  title: string;
  desc: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <section className="mb-3 rounded-panel border border-line bg-surface p-4">
      <h2 className="flex items-center gap-2 text-[14.5px] font-semibold text-ink">
        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-acc-soft font-mono text-[11px] font-bold text-acc">
          {n}
        </span>
        {title}
      </h2>
      <p className="mt-1.5 text-[13px] leading-relaxed text-ink-2">{desc}</p>
      {children}
    </section>
  );
}
