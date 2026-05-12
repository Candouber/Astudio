/**
 * 批注文字高亮 DOM 工具。
 * DeliverableList / ResultView / 其它含批注的展示组件共用，避免逻辑重复。
 */

export function clearHighlights(container: HTMLElement): void {
  container.querySelectorAll('mark.ann-hl').forEach((el) => {
    const parent = el.parentNode
    if (parent) {
      parent.replaceChild(document.createTextNode(el.textContent || ''), el)
      parent.normalize()
    }
  })
}

type IndexedChar = {
  node: Text
  offset: number
  endOffset: number
}

type TextIndex = {
  text: string
  map: IndexedChar[]
  nodes: Text[]
}

function collectTextNodes(container: HTMLElement): Text[] {
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) => {
      let p: Node | null = node.parentNode
      while (p && p !== container) {
        if (p.nodeType === 1 && (p as HTMLElement).tagName === 'MARK') {
          return NodeFilter.FILTER_REJECT
        }
        p = p.parentNode
      }
      return NodeFilter.FILTER_ACCEPT
    },
  })

  const textNodes: Text[] = []
  let n: Node | null
  while ((n = walker.nextNode())) textNodes.push(n as Text)
  return textNodes
}

function buildTextIndex(nodes: Text[], whitespace: 'collapse' | 'remove'): TextIndex {
  const chars: string[] = []
  const map: IndexedChar[] = []
  let lastWasWhitespace = false

  for (const node of nodes) {
    const value = node.textContent || ''
    for (let offset = 0; offset < value.length; offset += 1) {
      const ch = value[offset]
      const isWs = /\s/.test(ch)
      if (isWs) {
        if (whitespace === 'remove') continue
        if (lastWasWhitespace) continue
        chars.push(' ')
        map.push({ node, offset, endOffset: offset + 1 })
        lastWasWhitespace = true
      } else {
        chars.push(ch)
        map.push({ node, offset, endOffset: offset + 1 })
        lastWasWhitespace = false
      }
    }
  }

  return { text: chars.join(''), map, nodes }
}

function wrapMatch(index: TextIndex, start: number, length: number, annId: string): boolean {
  const first = index.map[start]
  const last = index.map[start + length - 1]
  if (!first || !last) return false

  const segments: Array<{ node: Text; startOffset: number; endOffset: number }> = []
  let active = false

  for (const node of index.nodes) {
    const valueLength = (node.textContent || '').length
    if (node === first.node && node === last.node) {
      segments.push({ node, startOffset: first.offset, endOffset: last.endOffset })
      break
    }
    if (node === first.node) {
      active = true
      segments.push({ node, startOffset: first.offset, endOffset: valueLength })
      continue
    }
    if (node === last.node) {
      segments.push({ node, startOffset: 0, endOffset: last.endOffset })
      break
    }
    if (active) {
      segments.push({ node, startOffset: 0, endOffset: valueLength })
    }
  }

  let wrapped = false
  for (let i = segments.length - 1; i >= 0; i -= 1) {
    const segment = segments[i]
    if (segment.endOffset <= segment.startOffset) continue
    const value = segment.node.textContent || ''
    if (!value.slice(segment.startOffset, segment.endOffset).trim()) continue

    const range = document.createRange()
    range.setStart(segment.node, segment.startOffset)
    range.setEnd(segment.node, segment.endOffset)
    const mark = document.createElement('mark')
    mark.className = 'ann-hl'
    mark.dataset.annId = annId
    mark.title = '点击查看批注'
    try {
      range.surroundContents(mark)
      wrapped = true
    } catch {
      /* 单个文本节点仍可能因浏览器 range 状态异常失败，跳过该片段。 */
    }
  }

  return wrapped
}

export function highlightText(container: HTMLElement, text: string, annId: string): void {
  const collapsedSearch = text.replace(/\s+/g, ' ').trim()
  if (!collapsedSearch || collapsedSearch.length < 2) return

  const textNodes = collectTextNodes(container)
  const collapsedIndex = buildTextIndex(textNodes, 'collapse')
  const collapsedStart = collapsedIndex.text.indexOf(collapsedSearch)
  if (collapsedStart !== -1 && wrapMatch(collapsedIndex, collapsedStart, collapsedSearch.length, annId)) {
    return
  }

  const compactSearch = text.replace(/\s+/g, '').trim()
  if (!compactSearch || compactSearch.length < 2) return
  const compactIndex = buildTextIndex(collectTextNodes(container), 'remove')
  const compactStart = compactIndex.text.indexOf(compactSearch)
  if (compactStart !== -1) {
    wrapMatch(compactIndex, compactStart, compactSearch.length, annId)
  }
}

export function applyHighlights(
  container: HTMLElement,
  anns: Array<{ id: string; selected_text: string }>
): void {
  clearHighlights(container)
  for (const ann of anns) {
    highlightText(container, ann.selected_text, ann.id)
  }
}

/** 构造用于 useEffect 依赖的稳定签名，避免父组件每次 render 产生新数组引用触发抖动。 */
export function annotationSignature(
  anns: Array<{ id: string; selected_text: string }>
): string {
  return anns.map((a) => `${a.id}:${a.selected_text}`).join('|')
}
