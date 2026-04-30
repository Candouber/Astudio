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

export function highlightText(container: HTMLElement, text: string, annId: string): void {
  const search = text.replace(/\s+/g, ' ').trim()
  if (!search || search.length < 2) return

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

  for (const tn of textNodes) {
    const nodeText = tn.textContent || ''
    const idx = nodeText.indexOf(search)
    if (idx === -1) continue
    const range = document.createRange()
    range.setStart(tn, idx)
    range.setEnd(tn, idx + search.length)
    const mark = document.createElement('mark')
    mark.className = 'ann-hl'
    mark.dataset.annId = annId
    mark.title = '点击查看批注'
    try {
      range.surroundContents(mark)
      return
    } catch {
      /* 继续尝试 */
    }
  }

  const normalizedAll = textNodes.map((tn) => tn.textContent || '').join('')
  const startInAll = normalizedAll.indexOf(search)
  if (startInAll === -1) return
  const endInAll = startInAll + search.length

  let acc = 0
  let startNode: Text | null = null
  let startOffset = 0
  let endNode: Text | null = null
  let endOffset = 0
  for (const tn of textNodes) {
    const len = (tn.textContent || '').length
    if (!startNode && startInAll < acc + len) {
      startNode = tn
      startOffset = startInAll - acc
    }
    if (!endNode && endInAll <= acc + len) {
      endNode = tn
      endOffset = endInAll - acc
      break
    }
    acc += len
  }
  if (!startNode || !endNode) return
  try {
    const range = document.createRange()
    range.setStart(startNode, startOffset)
    range.setEnd(endNode, endOffset)
    const mark = document.createElement('mark')
    mark.className = 'ann-hl'
    mark.dataset.annId = annId
    mark.title = '点击查看批注'
    try {
      range.surroundContents(mark)
    } catch {
      /* 跨块级边界失败 — 跳过 */
    }
  } catch {
    /* 无效 range — 跳过 */
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
