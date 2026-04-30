import { memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import type { Components } from 'react-markdown'
import 'highlight.js/styles/github.css'
import './MarkdownRenderer.css'

const components: Components = {
  a: ({ href, children, ...rest }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" {...rest}>
      {children}
    </a>
  ),
}

// 把插件数组提取成模块级常量，避免每次 render 都生成新引用触发 ReactMarkdown 重渲染
const REMARK_PLUGINS = [remarkGfm]
const REHYPE_PLUGINS = [rehypeHighlight]

interface Props {
  content: string
  className?: string
}

function MarkdownRendererImpl({ content, className = '' }: Props) {
  if (!content) return null

  return (
    <div className={`markdown-body ${className}`}>
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        rehypePlugins={REHYPE_PLUGINS}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

// markdown 渲染是 O(N) 且带 highlight.js，开销不小；同样 content 应避免重渲染
const MarkdownRenderer = memo(MarkdownRendererImpl)
export default MarkdownRenderer
