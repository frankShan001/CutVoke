/** Deliberate token matching: exact names precede categories, never loose letter subsequences. */
export interface SearchEntry { id: string; name: string; kind: 'effect' | 'preset' | 'command'; keywords?: string[]; }
const normalize = (value: string) => value.normalize('NFKD').replace(/\p{M}/gu, '').toLocaleLowerCase().trim();
export function rankQuickApply<T extends SearchEntry>(entries: T[], query: string, recent: string[] = [], favorites: string[] = []): T[] {
  const scoped = /^\s*([epm]):\s*/i.exec(query);
  const kind = scoped ? ({ e: 'effect', p: 'preset', m: 'command' } as const)[scoped[1].toLowerCase() as 'e' | 'p' | 'm'] : undefined;
  const needle = normalize(scoped ? query.slice(scoped[0].length) : query);
  const tokens = needle.split(/\s+/).filter(Boolean);
  return entries.flatMap(entry => {
    if (kind && entry.kind !== kind) return [];
    const name = normalize(entry.name), id = normalize(entry.id), words = (entry.keywords || []).map(normalize);
    const haystack = [name, id, ...words].join(' ');
    if (!tokens.every(token => haystack.includes(token))) return [];
    let score = !needle ? 0 : name === needle || id === needle ? 1000 : name.startsWith(needle) ? 800 :
      name.includes(needle) ? 600 : tokens.every(token => name.includes(token) || id.endsWith(`.${token}`)) ? 400 : 100;
    score += favorites.includes(entry.id) ? 20 : 0;
    const position = recent.indexOf(entry.id);
    if (position >= 0) score += Math.max(1, 15 - position);
    return [{ entry, score }];
  }).sort((a,b) => b.score-a.score || a.entry.name.localeCompare(b.entry.name)).map(item => item.entry);
}
