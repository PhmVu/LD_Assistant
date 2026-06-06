import { useEffect, useRef, useState, useCallback } from 'react'
import type { KeyboardEvent, RefObject } from 'react'
import LaneCanvas, { type DrawingInstruction } from '../../components/LaneCanvas'
import ParticleBackground from '../../components/ParticleBackground'
import SubNav from '../../components/SubNav'
import { useAuth } from '../../context/AuthContext'
import { useTheme } from '../../context/ThemeContext'
import { DASHBOARD_SUBNAV } from '../qaShared'
import './style.css'

type ChatMessage = { role: 'user' | 'assistant'; content: string; drawing?: DrawingInstruction | null; ts?: number }
type Conversation = { id: string; title: string; createdAt: number }

const LS_CONVERSATIONS = 'ld_chat_conversations'
const LS_MESSAGES = 'ld_chat_messages'

function saveToLS(convs: Conversation[], msgs: Record<string, ChatMessage[]>) {
  try {
    localStorage.setItem(LS_CONVERSATIONS, JSON.stringify(convs))
    localStorage.setItem(LS_MESSAGES, JSON.stringify(msgs))
  } catch { /* noop */ }
}

function loadFromLS(): { conversations: Conversation[]; convMessages: Record<string, ChatMessage[]> } {
  try {
    const convs = JSON.parse(localStorage.getItem(LS_CONVERSATIONS) || '[]') as Conversation[]
    const msgs = JSON.parse(localStorage.getItem(LS_MESSAGES) || '{}') as Record<string, ChatMessage[]>
    return { conversations: convs, convMessages: msgs }
  } catch {
    return { conversations: [], convMessages: {} }
  }
}

function formatTime(ts?: number) {
  if (!ts) return ''
  const d = new Date(ts)
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffMin = Math.floor(diffMs / 60000)
  const diffHr = Math.floor(diffMs / 3600000)
  const diffDay = Math.floor(diffMs / 86400000)
  if (diffMin < 1) return 'Vừa xong'
  if (diffMin < 60) return `${diffMin} phút trước`
  if (diffHr < 24) return `${diffHr} giờ trước`
  if (diffDay < 7) return `${diffDay} ngày trước`
  return d.toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit' })
}

function renderMarkdownProse(text: string): JSX.Element {
  if (!text) return <></>

  // Step 1: Split into code blocks vs non-code blocks
  const parts = text.split('```')
  const elements: JSX.Element[] = []
  let key = 0

  for (let i = 0; i < parts.length; i += 1) {
    const part = parts[i]
    if (i % 2 === 1) {
      // Code block
      const lines = part.split('\n')
      let lang = lines[0].trim().toLowerCase()
      let codeLines = lines.slice(1)
      if (lang && !['const', 'let', 'var', 'function', 'class', 'import', 'def', '{', '[', '"'].includes(lang)) {
        // Valid language name
      } else {
        lang = 'code'
        codeLines = lines
      }
      const codeText = codeLines.join('\n').trim()

      elements.push(
        <div key={key += 1} className="markdown-code-block">
          <div className="code-block-header">
            <span className="code-lang">{lang.toUpperCase()}</span>
            <button
              className="copy-code-btn"
              onClick={(e) => {
                const btn = e.currentTarget
                void navigator.clipboard.writeText(codeText)
                btn.innerText = 'Đã sao chép!'
                setTimeout(() => { btn.innerText = 'Sao chép' }, 2000)
              }}
            >
              Sao chép
            </button>
          </div>
          <pre><code>{codeText}</code></pre>
        </div>
      )
      continue
    }

    // Process prose
    const lines = part.split('\n')
    let listItems: JSX.Element[] = []
    let listType: 'ul' | 'ol' | null = null
    let tableRows: string[][] = []
    let isTable = false

    const flushList = () => {
      if (listItems.length > 0 && listType) {
        const CurrentType = listType
        const items = [...listItems]
        elements.push(
          <CurrentType key={key += 1} className={`markdown-list ${CurrentType}`}>
            {items}
          </CurrentType>
        )
        listItems = []
        listType = null
      }
    }

    const flushTable = () => {
      if (tableRows.length > 0) {
        const rows = [...tableRows]
        // Determine headers
        const hasHeaderDivider = rows.length > 1 && rows[1].every(cell => cell.trim().startsWith('-') || cell.trim() === '')
        const headerRow = rows[0]
        const bodyRows = hasHeaderDivider ? rows.slice(2) : rows.slice(1)

        elements.push(
          <div key={key += 1} className="markdown-table-wrapper">
            <table className="markdown-table">
              <thead>
                <tr>
                  {headerRow.map((cell, idx) => (
                    <th key={idx}>{inlineFormat(cell.trim())}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {bodyRows.map((row, rIdx) => (
                  <tr key={rIdx}>
                    {row.map((cell, cIdx) => (
                      <td key={cIdx}>{inlineFormat(cell.trim())}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
        tableRows = []
        isTable = false
      }
    }

    for (let j = 0; j < lines.length; j += 1) {
      const line = lines[j]
      const trimmed = line.trim()

      // Table line matching
      if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
        flushList()
        isTable = true
        // Split and filter empty outer elements
        const cells = line.split('|').slice(1, -1)
        tableRows.push(cells)
        continue
      } else {
        if (isTable) {
          flushTable()
        }
      }

      if (!trimmed) {
        flushList()
        elements.push(<div key={key += 1} className="markdown-para-spacing" />)
        continue
      }

      // Headings
      const headingMatch = line.match(/^(#{1,6})\s+(.+)$/)
      if (headingMatch) {
        flushList()
        const level = headingMatch[1].length
        const content = headingMatch[2]
        elements.push(
          <div key={key += 1} className={`markdown-heading h${level}`}>
            {inlineFormat(content)}
          </div>
        )
        continue
      }

      // Blockquote
      const quoteMatch = line.match(/^>\s*(.+)$/)
      if (quoteMatch) {
        flushList()
        elements.push(
          <blockquote key={key += 1} className="markdown-quote">
            {inlineFormat(quoteMatch[1])}
          </blockquote>
        )
        continue
      }

      // Bullet List
      const bulletMatch = line.match(/^[-*+]\s+(.+)$/)
      if (bulletMatch) {
        if (listType !== 'ul') {
          flushList()
          listType = 'ul'
        }
        listItems.push(
          <li key={listItems.length}>{inlineFormat(bulletMatch[1])}</li>
        )
        continue
      }

      // Numbered List
      const numberedMatch = line.match(/^(\d+)[.)]\s+(.+)$/)
      if (numberedMatch) {
        if (listType !== 'ol') {
          flushList()
          listType = 'ol'
        }
        listItems.push(
          <li key={listItems.length} value={parseInt(numberedMatch[1], 10)}>
            {inlineFormat(numberedMatch[2])}
          </li>
        )
        continue
      }

      // Plain text (part of a paragraph or single line)
      flushList()
      elements.push(
        <p key={key += 1} className="markdown-p">
          {inlineFormat(line)}
        </p>
      )
    }

    flushList()
    flushTable()
  }

  return <div className="markdown-body">{elements}</div>
}

function inlineFormat(text: string): (JSX.Element | string)[] {
  const parts: (JSX.Element | string)[] = []
  const re = /\*\*(.+?)\*\*|__(.+?)__|`(.+?)`/g
  let last = 0
  let match: RegExpExecArray | null
  let key = 0
  while ((match = re.exec(text)) !== null) {
    if (match.index > last) {
      parts.push(text.slice(last, match.index))
    }
    if (match[1] || match[2]) {
      // Bold
      parts.push(
        <strong key={key += 1} className="inline-bold">
          {match[1] ?? match[2]}
        </strong>
      )
    } else if (match[3]) {
      // Inline code
      parts.push(
        <code key={key += 1} className="inline-code">
          {match[3]}
        </code>
      )
    }
    last = match.index + match[0].length
  }
  if (last < text.length) {
    parts.push(text.slice(last))
  }
  return parts.length ? parts : [text]
}

const QUICK_ACTIONS = [
  { icon: '🛣️', label: 'Quy tắc vạch làn đường' },
  { icon: '✏️', label: 'Phân tích lỗi annotation' },
  { icon: '📐', label: 'Tiêu chuẩn kỹ thuật vạch kẻ' },
]

type InputBoxProps = {
  centered?: boolean
  input: string
  setInput: (val: string) => void
  textareaRef: RefObject<HTMLTextAreaElement | null>
  fileInputRef: RefObject<HTMLInputElement | null>
  isStreaming: boolean
  sendMessage: (text?: string) => void
  handleKeyDown: (e: KeyboardEvent<HTMLTextAreaElement>) => void
  pendingFile: File | null
  setPendingFile: (file: File | null) => void
}

function InputBox({ centered, input, setInput, textareaRef, fileInputRef, isStreaming, sendMessage, handleKeyDown, pendingFile, setPendingFile }: InputBoxProps) {
  return (
    <div className={`input-wrapper ${centered ? 'centered' : 'bottom'}`}>
      <div className="chat-input-box">
        <button className="input-action-btn" title="Đính kèm ảnh" onClick={() => fileInputRef.current?.click()}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
          </svg>
        </button>
        <textarea
          ref={textareaRef}
          className="chat-textarea"
          placeholder="Hỏi bất kỳ điều gì về annotation vạch đường..."
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={handleKeyDown}
          rows={1}
        />
        <button className="send-btn" onClick={() => sendMessage()} disabled={!input.trim() || isStreaming}>
          {isStreaming ? (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
          ) : (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <line x1="22" y1="2" x2="11" y2="13" />
              <polygon points="22 2 15 22 11 13 2 9 22 2" />
            </svg>
          )}
        </button>
      </div>
      {pendingFile ? (
        <div className="pending-file">
          <span>📎 {pendingFile.name}</span>
          <button onClick={() => setPendingFile(null)}>✕</button>
        </div>
      ) : null}
    </div>
  )
}

export default function ChatPage() {
  const { theme } = useTheme()
  const { authFetch, user } = useAuth()

  // Load initial state from localStorage
  const [conversations, setConversations] = useState<Conversation[]>(() => loadFromLS().conversations)
  const [convMessages, setConvMessages] = useState<Record<string, ChatMessage[]>>(() => loadFromLS().convMessages)
  const [activeConvId, setActiveConvId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [pendingFile, setPendingFile] = useState<File | null>(null)
  const [drawingFading, setDrawingFading] = useState(false)

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const convMessagesRef = useRef(convMessages)
  const abortControllerRef = useRef<AbortController | null>(null)

  useEffect(() => { convMessagesRef.current = convMessages }, [convMessages])

  // Persist to localStorage whenever state changes
  useEffect(() => {
    saveToLS(conversations, convMessages)
  }, [conversations, convMessages])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`
  }, [input])

  useEffect(() => {
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort()
      }
    }
  }, [])

  function switchConversation(id: string) {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
    }
    setIsStreaming(false)
    setActiveConvId(id)
    setMessages(convMessagesRef.current[id] ?? [])
    setInput('')
  }

  function newChat() {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
    }
    setIsStreaming(false)
    setActiveConvId(null)
    setMessages([])
    setInput('')
    setTimeout(() => textareaRef.current?.focus(), 50)
  }

  const deleteConversation = useCallback((id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setConversations(prev => prev.filter(c => c.id !== id))
    setConvMessages(prev => {
      const next = { ...prev }
      delete next[id]
      return next
    })
    if (activeConvId === id) {
      setActiveConvId(null)
      setMessages([])
    }
  }, [activeConvId])

  async function sendMessage(text?: string) {
    const content = (text ?? input).trim()
    if (!content || isStreaming) return

    const userMsg: ChatMessage = { role: 'user', content, ts: Date.now() }
    let currentId = activeConvId

    if (!currentId) {
      currentId = String(Date.now())
      setActiveConvId(currentId)
      setConversations((current) => [{ id: currentId!, title: content.slice(0, 45), createdAt: Date.now() }, ...current])
    }

    setMessages((current) => {
      const next = [...current, userMsg]
      setConvMessages((prev) => ({ ...prev, [currentId!]: next }))
      return next
    })
    setInput('')
    setIsStreaming(true)

    setMessages((current) => {
      const next = [...current, { role: 'assistant' as const, content: '', ts: Date.now() }]
      setConvMessages((prev) => ({ ...prev, [currentId!]: next }))
      return next
    })

    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }
    const controller = new AbortController()
    abortControllerRef.current = controller

    try {
      const formData = new FormData()
      formData.append('message', content)
      formData.append(
        'history',
        JSON.stringify(messages.slice(-6).map((message) => ({ role: message.role, content: message.content }))),
      )
      if (pendingFile) {
        formData.append('image', pendingFile)
        setPendingFile(null)
      }

      const response = await authFetch('/api/ld/chat/stream', {
        method: 'POST',
        body: formData,
        signal: controller.signal,
      })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)

      const reader = response.body?.getReader()
      if (!reader) throw new Error('Stream not available')

      const decoder = new TextDecoder()
      let buffer = ''
      let fullText = ''
      let pendingDrawing: DrawingInstruction | null = null

      const attachDrawing = (drawing: DrawingInstruction) => {
        setMessages((current) => {
          const next = [...current]
          const last = { ...next[next.length - 1] }
          last.drawing = drawing
          next[next.length - 1] = last
          setConvMessages((prev) => ({ ...prev, [currentId!]: next }))
          return next
        })
      }

      while (true) {
        const { value, done } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          if (!line.startsWith('data:')) continue
          const raw = line.slice(5).trim()
          if (!raw || raw === '[DONE]') continue

          try {
            const event = JSON.parse(raw)
            if (event.type === 'drawing' || event.type === 'drawing_refined') {
              const drawing = event.drawing
              const preferred = drawing?.base?.style ? drawing.base : drawing?.variants?.[0]?.style ? drawing.variants[0] : drawing
              if (event.type === 'drawing_refined') {
                setDrawingFading(true)
                setTimeout(() => { attachDrawing(preferred); setDrawingFading(false) }, 280)
              } else {
                pendingDrawing = preferred
                if (fullText.length >= 80 && pendingDrawing) { attachDrawing(pendingDrawing); pendingDrawing = null }
              }
            } else if (event.type === 'token') {
              fullText += event.text
              setMessages((current) => {
                const next = [...current]
                const last = { ...next[next.length - 1] }
                last.content = fullText
                next[next.length - 1] = last
                setConvMessages((prev) => ({ ...prev, [currentId!]: next }))
                return next
              })
              if (pendingDrawing && fullText.length >= 80) { attachDrawing(pendingDrawing); pendingDrawing = null }
            } else if (event.type === 'done' && pendingDrawing) {
              attachDrawing(pendingDrawing)
              pendingDrawing = null
            }
          } catch { /* Ignore partial SSE events */ }
        }
      }

      if (pendingDrawing) attachDrawing(pendingDrawing)

      if (!fullText) {
        setMessages((current) => {
          const next = [...current]
          next[next.length - 1] = { ...next[next.length - 1], content: 'Xin lỗi, mình chưa nhận được phản hồi hợp lệ. Bạn thử lại giúp mình nhé.' }
          setConvMessages((prev) => ({ ...prev, [currentId!]: next }))
          return next
        })
      }
    } catch (err: any) {
      if (err.name === 'AbortError') return

      setMessages((current) => {
        const next = [...current]
        next[next.length - 1] = { ...next[next.length - 1], content: 'Đường truyền tới AI đang lỗi. Mình đã giữ nguyên cuộc trò chuyện, bạn thử gửi lại nhé.' }
        setConvMessages((prev) => ({ ...prev, [currentId!]: next }))
        return next
      })
    } finally {
      if (abortControllerRef.current === controller) {
        setIsStreaming(false)
        abortControllerRef.current = null
      }
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void sendMessage()
    }
  }

  const identityLabel = user?.display_name || user?.username?.replace(/^jr-/, '').replace(/-ty$/, '') || 'LD member'
  const userInitials = identityLabel.slice(0, 2).toUpperCase()

  return (
    <div className={`chat-layout ${theme}`}>
      <ParticleBackground densityMultiplier={0.8} maxParticles={44} connectionDistance={120} speedMultiplier={0.8} zIndex={0} />
      <div className="chat-layer">
        <SubNav items={DASHBOARD_SUBNAV} />
        <div className="chat-workspace">
          <aside className="chat-sidebar">
            {/* Sidebar Header */}
            <div className="chat-sidebar-header">
              <div className="chat-sidebar-brand">
                <div className="chat-brand-icon">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                  </svg>
                </div>
                <div>
                  <strong className="chat-brand-title">Chat LD</strong>
                  <p className="chat-brand-sub">AI hỗ trợ annotation vạch đường</p>
                </div>
              </div>

            </div>

            {/* New Chat Button */}
            <div className="chat-sidebar-actions">
              <button className="new-chat-btn" onClick={newChat}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                  <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                </svg>
                Đoạn chat mới
              </button>
            </div>

            {/* History Section */}
            <div className="chat-history-section">
              <span className="history-label">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ marginRight: '6px' }}>
                  <circle cx="12" cy="12" r="10" />
                  <polyline points="12 6 12 12 16 14" />
                </svg>
                Gần đây
                {conversations.length > 0 && <span className="history-count">{conversations.length}</span>}
              </span>
              <div className="history-list">
                {conversations.length === 0 && (
                  <div className="history-empty">
                    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" style={{ opacity: 0.3 }}>
                      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                    </svg>
                    <span>Chưa có cuộc trò chuyện nào</span>
                  </div>
                )}
                {conversations.map((conversation) => (
                  <div
                    key={conversation.id}
                    className={`history-item ${activeConvId === conversation.id ? 'active' : ''}`}
                    onClick={() => switchConversation(conversation.id)}
                  >
                    <div className="history-item-content">
                      <span className="history-item-title">{conversation.title}</span>
                      <span className="history-item-time">{formatTime(conversation.createdAt)}</span>
                    </div>
                    <button
                      className="history-item-delete"
                      onClick={(e) => deleteConversation(conversation.id, e)}
                      title="Xóa cuộc trò chuyện"
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                        <line x1="18" y1="6" x2="6" y2="18" />
                        <line x1="6" y1="6" x2="18" y2="18" />
                      </svg>
                    </button>
                  </div>
                ))}
              </div>
            </div>

            {/* Footer */}
            <div className="chat-sidebar-footer">
              <div className="user-badge" title="Phiên đăng nhập hiện tại">
                <div className="user-avatar">{userInitials}</div>
                <div className="user-info">
                  <span className="user-name">{identityLabel}</span>
                  <span className="user-plan">AI hỗ trợ nội bộ</span>
                </div>
                <div className="user-status-dot" />
              </div>
            </div>
          </aside>

          <main className="chat-main">
            {messages.length === 0 ? (
              <div className="chat-welcome">
                <div className="chat-welcome-copy">
                  <span className="chat-eyebrow">Hỗ trợ annotation</span>
                  <h1 className="welcome-title">Hôm nay bạn muốn AI hỗ trợ phần nào?</h1>
                  <p className="chat-subcopy">Bạn có thể hỏi tiêu chuẩn, phân tích lỗi hoặc gửi ảnh để AI minh họa trực tiếp vạch cần soát.</p>
                </div>
                <InputBox
                  centered
                  input={input}
                  setInput={setInput}
                  textareaRef={textareaRef}
                  fileInputRef={fileInputRef}
                  isStreaming={isStreaming}
                  sendMessage={sendMessage}
                  handleKeyDown={handleKeyDown}
                  pendingFile={pendingFile}
                  setPendingFile={setPendingFile}
                />
                <div className="quick-actions">
                  {QUICK_ACTIONS.map((action) => (
                    <button key={action.label} className="quick-btn" onClick={() => void sendMessage(action.label)}>
                      <span>{action.icon}</span>
                      <span>{action.label}</span>
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="chat-conversation">
                <div className="messages-list">
                  {messages.map((message, index) => (
                    <div key={`${message.role}-${index}`} className={`message-row ${message.role}`}>
                      {message.role === 'assistant' ? (
                        <div className="msg-avatar ai-avatar">
                          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2V9M9 21H5a2 2 0 0 1-2-2V9m0 0h18" />
                          </svg>
                        </div>
                      ) : (
                        <div className="msg-avatar user-msg-avatar">{userInitials}</div>
                      )}
                      <div className="msg-content-wrap">
                        <div className="msg-bubble">
                          <div className="msg-text">
                            {message.role === 'assistant' ? renderMarkdownProse(message.content) : message.content}
                            {message.role === 'assistant' && isStreaming && index === messages.length - 1 ? (
                              <span className="cursor-blink">○</span>
                            ) : null}
                          </div>
                          {message.role === 'assistant' && message.drawing ? (
                            <div className="msg-drawing" style={{ opacity: drawingFading ? 0 : 1, transition: 'opacity 0.28s ease' }}>
                              <div className="msg-drawing-title">
                                <span />
                                <small>Minh họa lane</small>
                                <span />
                              </div>
                              <LaneCanvas data={message.drawing} size="full" />
                            </div>
                          ) : null}
                        </div>
                        {message.ts && (
                          <span className="msg-time">{formatTime(message.ts)}</span>
                        )}
                      </div>
                    </div>
                  ))}
                  <div ref={messagesEndRef} />
                </div>
                <InputBox
                  input={input}
                  setInput={setInput}
                  textareaRef={textareaRef}
                  fileInputRef={fileInputRef}
                  isStreaming={isStreaming}
                  sendMessage={sendMessage}
                  handleKeyDown={handleKeyDown}
                  pendingFile={pendingFile}
                  setPendingFile={setPendingFile}
                />
              </div>
            )}
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              style={{ display: 'none' }}
              onChange={(event) => setPendingFile(event.target.files?.[0] ?? null)}
            />
          </main>
        </div>
      </div>
    </div>
  )
}
