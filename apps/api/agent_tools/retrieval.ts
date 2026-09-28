/**
 * 中文/混合查询的分词与匹配（B08）。
 *
 * 为什么用二元切分（bigram）而不是整词或词典分词：
 * - 原实现按 `split(/\s+/)` 切词，中文没有空格，整句变成单个 term，
 *   拿去 `includes` 一段中文几乎必然为假 —— 中文检索 100% 零命中。
 * - 完整词典分词需要词典/模型，属于外部依赖或重资产，本机离线装不起。
 * - bigram（相邻两字滑窗）不依赖词典：把「怎么提高剪辑效率」切成
 *   怎么/么提/提高/高剪/剪辑/辑效/效率，任一子串出现在文本里即算命中。
 *   对中文短查询召回足够，且实现只有几行、零依赖。
 * - 英文/数字/标签（camera、drone、4k）按空白/非中文边界整词保留原样，
 *   不被 bigram 打散，避免英文检索回归。
 *
 * 匹配用「命中子串数 / 子串总数」打分排序，命中不到任何子串才落空；
 * 落空时也不直接吞掉，而是由调用方给出行内引导语（见 diary_tools.ts）。
 */

/** 判断是否 CJK 字符（中日韩统一表意文字 + 常用扩展区）。 */
function isCjk(ch: string): boolean {
  const code = ch.codePointAt(0) ?? 0;
  return (
    (code >= 0x4e00 && code <= 0x9fff) || // CJK 统一表意
    (code >= 0x3400 && code <= 0x4dbf) || // 扩展 A
    (code >= 0xf900 && code <= 0xfaff) || // 兼容表意
    (code >= 0x3040 && code <= 0x30ff) // 日文假名（并入处理，避免落空）
  );
}

/**
 * 把查询切成检索子串。
 * - 连续的 CJK 串 → 长度>=2 切成 bigram 滑窗；长度为 1 的单字直接保留。
 * - 非 CJK 段（英文/数字/标签）按非字母数字边界整词保留，转小写。
 */
export function tokenizeQuery(query: string): string[] {
  const normalized = (query || "").toLowerCase().trim();
  if (!normalized) return [];
  const terms: string[] = [];
  // 先把查询切成「CJK 段」与「非 CJK 段」两类
  const segments = normalized.match(/[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]+|[^㐀-鿿぀-ヿ\s]+/gu) || [];
  for (const segment of segments) {
    const chars = [...segment];
    const cjk = chars.every((ch) => isCjk(ch));
    if (cjk) {
      if (chars.length === 1) {
        terms.push(chars[0]);
      } else {
        for (let i = 0; i + 1 < chars.length; i += 1) {
          terms.push(chars[i] + chars[i + 1]);
        }
        // 整串也保留，提高长句整体匹配的权重
        terms.push(segment);
      }
    } else {
      // 英文/数字/标签：整词保留（已 lower）
      terms.push(segment);
    }
  }
  return terms.filter(Boolean);
}

/**
 * 给一段文本打分：命中的子串个数与命中子串列表。
 * 大小写不敏感。空查询返回 0 分。
 */
export function scoreText(query: string, text: string): { score: number; hitTerms: string[] } {
  const terms = tokenizeQuery(query);
  if (terms.length === 0) return { score: 0, hitTerms: [] };
  const haystack = (text || "").toLowerCase();
  const hitTerms = terms.filter((term) => haystack.includes(term));
  // 去重后按命中比例打分；整串精确命中额外加权
  const uniqueHits = [...new Set(hitTerms)];
  let score = uniqueHits.length / terms.length;
  if (query && haystack.includes(query.toLowerCase().trim())) {
    score += 0.5; // 整句命中，强信号
  }
  return { score, hitTerms: uniqueHits };
}

/** 从候选集合里挑出命中项并按分排序；命中不到时返回空数组（由调用方补引导语）。 */
export function rankByQuery<T>(
  query: string,
  items: T[],
  textOf: (item: T) => string,
): Array<{ item: T; score: number; hitTerms: string[] }> {
  const scored = items
    .map((item) => {
      const { score, hitTerms } = scoreText(query, textOf(item));
      return { item, score, hitTerms };
    })
    .filter((entry) => entry.score > 0);
  scored.sort((a, b) => b.score - a.score);
  return scored;
}
