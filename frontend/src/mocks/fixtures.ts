/** Deterministic mock fixtures derived from the real API contract
 * (lib/api-types.ts). Seeded RNG => reproducible scenarios at any scale
 * (100 / 1,000 / 10,000 evidence events) for perf and edge testing. */
import type {
  ConflictsResponse,
  EventsResponse,
  FacetsResponse,
  HealthResponse,
  ReportsResponse,
  SearchResponse,
  TimelineEvent,
  VideoRow,
  VideosResponse,
  WorkbenchAtom,
} from "@/lib/api-types";

export function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const ASR_TEXTS = [
  "卫生间墙面防水要达到两米，并且覆盖用水区域",
  "地面要采用柔性防水，墙面用刚性防水，防止贴砖空鼓",
  "防水总厚度要在1.2毫米以上，涂刷三遍",
  "施工完成后要及时做闭水试验，24小时后到楼下查看",
  "水电改造管线位置要先用水泥砂浆覆盖，再做二次防水",
  "瓷砖留缝建议三毫米，国标对边长大于800毫米的砖有要求",
  "美缝要在瓷砖干透之后做，半凝固状态就不要再刮了",
];
const OCR_TEXTS = [
  "室内卫生间防水工程所有施工工序",
  "建筑与市政工程防水通用规范",
  "伸缩缝间距不宜大于8m，宽度宜为5mm~10mm",
  "JC/T 60006-2020 瓷砖薄贴法施工技术规程",
];
const VIS_TEXTS = [
  "画面:师傅在卫生间墙面滚涂防水涂料 | 字幕:\"第二遍涂刷方向与第一遍垂直\"",
  "画面:地漏周边做圆弧加强处理",
  "画面:展示瓷砖十字卡子，特写留缝宽度",
  "画面:防水涂层表面泛白，成膜完整",
];

const TITLES = [
  "6分钟看完卫生间JS防水所有施工工序",
  "瓷砖留缝1毫米还是3毫米？国标这么说",
  "水电改造避坑指南：横平竖直就是对的吗",
  "美缝自己动手教程，工具不到一百块",
  "乳胶漆怎么选？老师傅只看这三个指标",
  "全屋插座布局清单，装错一个都难受",
];
const AUTHORS = ["自远匠造", "卿卿带你玩儿装修", "老王小课堂", "装修避坑指南"];

export function makeVideos(n = 8): VideosResponse {
  const rnd = mulberry32(42);
  const statuses = ["processed", "processed", "processed", "imported", "error"];
  const videos: VideoRow[] = Array.from({ length: n }, (_, i) => ({
    video_id: `BVmock${String(i).padStart(3, "0")}`,
    title: TITLES[i % TITLES.length],
    author: AUTHORS[i % AUTHORS.length],
    status: statuses[i % statuses.length],
    duration_ms: 90_000 + Math.floor(rnd() * 300_000),
    imported_at: `2026-09-${String(10 + (i % 10)).padStart(2, "0")}T1${i % 10}:2${i % 10}:00+08:00`,
    source_platform: i % 3 === 0 ? "douyin" : "bilibili",
    atoms: Math.floor(rnd() * 12),
    error_detail: statuses[i % statuses.length] === "error" ? "atomize: LLM 429 quota exceeded" : null,
  }));
  return {
    videos,
    stats: {
      videos: n,
      atoms: 130,
      clusters: 3,
      conflicts: 6,
      conflicts_open: 4,
      jobs: { done: n, cancelled: 2 },
    },
  };
}

export function makeMeta(videoId: string) {
  return {
    video_id: videoId,
    title: TITLES[Math.abs(hashCode(videoId)) % TITLES.length],
    author: AUTHORS[Math.abs(hashCode(videoId)) % AUTHORS.length],
    status: "processed",
    duration_ms: 350_000,
    imported_at: "2026-09-18T22:10:00+08:00",
    source_platform: "bilibili",
    source_url: "https://www.bilibili.com/video/" + videoId,
    has_media: true,
    atoms: 7,
  };
}

function hashCode(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  return h;
}

export function makeEvents(videoId: string, count = 120): EventsResponse {
  const rnd = mulberry32(hashCode(videoId) || 7);
  const duration = Math.max(60_000, count * 2500);
  const events: TimelineEvent[] = Array.from({ length: count }, (_, i) => {
    const roll = rnd();
    const mod: TimelineEvent["mod"] = roll < 0.55 ? "ASR" : roll < 0.85 ? "OCR" : "VIS";
    const ms = Math.floor((i / count) * duration + rnd() * 2000);
    const end = mod === "VIS" ? ms + 1200 : ms + 2000 + Math.floor(rnd() * 4000);
    const text =
      mod === "ASR"
        ? ASR_TEXTS[i % ASR_TEXTS.length]
        : mod === "OCR"
          ? OCR_TEXTS[i % OCR_TEXTS.length]
          : VIS_TEXTS[i % VIS_TEXTS.length];
    return { id: `ev_${String(i).padStart(5, "0")}`, ms, end, mod, text };
  }).sort((a, b) => a.ms - b.ms);

  // a handful of atoms referencing real evidence ids
  const atoms: WorkbenchAtom[] = Array.from({ length: 7 }, (_, i) => {
    const e1 = events[Math.floor((i * count) / 7 + 1)] ?? events[0];
    const e2 = events[Math.floor((i * count) / 7 + 4)] ?? events[0];
    return {
      id: `atom_mock_${String(i).padStart(3, "0")}`,
      category: ["防水", "泥瓦", "水电", "涂装"][i % 4],
      space: ["卫生间", "全屋", "厨房"][i % 3],
      claim: ASR_TEXTS[i % ASR_TEXTS.length],
      polarity: ["recommend", "require", "avoid", "neutral", "optional"][i % 5],
      confidence: 0.6 + (i % 4) * 0.1,
      status: i === 2 ? "disputed" : "candidate",
      cluster_id: i === 2 ? "cluster_001" : null,
      parameters:
        i % 2 === 0
          ? [{ name: "涂刷厚度", value: "1.2", unit: "mm", param_status: "normalized" }]
          : [],
      evidence: [e1, e2].map((e) => ({
        id: e.id,
        ms: e.ms,
        end: e.end,
        mod: e.mod,
        text: e.text,
      })),
    };
  });
  return { events, atoms };
}

export const makeConflicts = (): ConflictsResponse => ({
  conflicts: [
    {
      conflict_id: "conflict_mock_001",
      ctype: "method_conflict",
      status: "needs_review",
      side_a: {
        atoms: ["atom_mock_000"],
        claim: "美缝应在材料半凝固状态时刮平",
        conditions: { space: "卫生间" },
        video: "BVmock000",
      },
      side_b: {
        atoms: ["atom_mock_003"],
        claim: "半凝固状态不应再操作,干透后再施工",
        conditions: { space: "卫生间" },
        video: "BVmock003",
      },
      analysis: {
        conditional_conclusion: { condition_overlap: true, scope_split: "", authority_gap: "" },
        judge_note: "两说分歧集中在操作时机判断",
      },
      recommended_action: "user_review",
      created_at: "2026-09-19T23:00:00+08:00",
    },
    {
      conflict_id: "conflict_mock_002",
      ctype: "numeric_conflict",
      status: "decided:accept_b",
      side_a: {
        atoms: ["atom_mock_001"],
        claim: "瓷砖留缝 1mm 即可",
        conditions: { space: "客厅" },
        video: "BVmock001",
      },
      side_b: {
        atoms: ["atom_mock_002"],
        claim: "瓷砖留缝不应小于 3mm(边长≥800mm)",
        conditions: { space: "全屋" },
        video: "BVmock002",
      },
      analysis: {
        conditional_conclusion: {
          condition_overlap: false,
          scope_split: "边长差异",
          authority_gap: "B 引用 JC/T 60006-2020",
        },
        judge_note: "B 有标准引用支撑",
      },
      recommended_action: "user_review",
      created_at: "2026-09-19T23:10:00+08:00",
    },
  ],
});

export const makeFacets = (): FacetsResponse => ({
  categories: ["水电", "泥瓦", "涂装", "电气", "防水", "验收"],
  spaces: ["全屋", "卫生间", "厨房", "客厅", "阳台"],
});

const SEARCHABLE = ["防水", "瓷砖", "美缝", "水电", "插座", "留缝"];

export const makeSearch = (q: string): SearchResponse => {
  if (!SEARCHABLE.some((k) => q.includes(k))) return { results: [] };
  return {
    results: [
      {
        id: "atom_mock_000",
        claim: `卫生间墙面防水要达到两米，并且覆盖用水区域（含“${q}”）`,
        category: "防水",
        space: "卫生间",
        polarity: "require",
        status: "candidate",
        video: "BVmock000",
        video_title: TITLES[0],
        ms: 42_000,
        mod: "ASR",
        evidence_text: "卫生间墙面防水要达到两米",
      },
    ],
  };
};

export const makeReports = (): ReportsResponse => ({
  reports: {
    checklist:
      "# 验收清单\n\n## 水电\n- [x] 强弱电分槽间距 ≥300mm\n- [ ] 水管打压试验记录\n\n## 防水\n| 项目 | 要求 | 结果 |\n|---|---|---|\n| 墙面高度 | ≥2000mm | 待验 |\n\n> 依据 `/videos/BVmock000?t=42000` 可跳转证据。\n",
    conflicts: "## 争议汇总\n\n- conflict_mock_001:美缝时机分歧(待复核)\n",
    incremental_diff: "本次运行无新增视频。\n",
  },
});

export const makeHealth = (): HealthResponse => ({
  ok: true,
  cookies_configured: true,
  vlm_enabled: false,
  atomize_model: "glm-4.5-flash(mock)",
});

// 1x1 gray JPEG for /api/frame mocks
export const TINY_JPEG = Uint8Array.from(
  atob(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwcJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPDs0NDT/wAALCAABAAEBAREA/8QAFAABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AJgA/9k=",
  ),
  (c) => c.charCodeAt(0),
);
