/** 搜索框关键词解析：支持用分号分隔多个关键词，命中任意一个即匹配。 */

const KEYWORD_SEPARATOR = /[;；]+/

export function splitKeywords(input: string | null | undefined): string[] {
  if (!input) return []
  const terms: string[] = []
  const seen = new Set<string>()
  for (const chunk of input.split(KEYWORD_SEPARATOR)) {
    const term = chunk.trim()
    if (!term) continue
    const key = term.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    terms.push(term)
  }
  return terms
}

export function hasKeyword(input: string | null | undefined): boolean {
  return splitKeywords(input).length > 0
}

/**
 * 分号分隔的多个关键词与多个字段做 OR 匹配：
 * 任一关键词命中任一字段即视为匹配；没有关键词时恒为 true。
 */
export function matchesAnyKeyword(
  input: string | null | undefined,
  values: Array<string | null | undefined>,
): boolean {
  const terms = splitKeywords(input)
  if (terms.length === 0) return true
  const haystack = values
    .filter((value): value is string => Boolean(value))
    .map((value) => value.toLowerCase())
  return terms.some((term) => {
    const needle = term.toLowerCase()
    return haystack.some((value) => value.includes(needle))
  })
}
