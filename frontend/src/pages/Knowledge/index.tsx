import { useEffect, useMemo, useRef, useState } from 'react'
import ParticleBackground from '../../components/ParticleBackground'
import SubNav from '../../components/SubNav'
import { useAuth } from '../../context/AuthContext'
import { useTheme } from '../../context/ThemeContext'
import { DASHBOARD_SUBNAV } from '../qaShared'
import './style.css'

type DocRecord = {
  id: string
  name: string
  type: string
  size: number
  uploaded_at: string
  summary?: string
  preview?: string
}

function fmtSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1_048_576) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1_048_576).toFixed(1)} MB`
}

function fmtDate(iso: string) {
  try {
    return new Date(iso).toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' })
  } catch {
    return iso
  }
}

function normaliseDocs(payload: any): DocRecord[] {
  const list = Array.isArray(payload)
    ? payload
    : Array.isArray(payload?.docs)
      ? payload.docs
      : Array.isArray(payload?.files)
        ? payload.files
        : Array.isArray(payload?.data)
          ? payload.data
          : []

  return list.map((doc: any, index: number) => {
    const ext = doc.name ? doc.name.split('.').pop()?.toLowerCase() || '' : ''
    return {
      id: doc.id || doc.name || `doc-${index}`,
      name: doc.name || 'Tai lieu khong ten',
      type: doc.type || ext || 'unknown',
      size: Number(doc.size || 0),
      uploaded_at: doc.uploaded_at || doc.updated || new Date().toISOString(),
      summary: doc.summary || '',
      preview: doc.preview || '',
    }
  })
}

function renderDocMarkdown(text: string): JSX.Element {
  if (!text) return <></>
  const lines = text.split('\n')
  const elements: JSX.Element[] = []
  let key = 0
  let listItems: JSX.Element[] = []
  let listType: 'ul' | 'ol' | null = null

  const flushList = () => {
    if (listItems.length > 0 && listType) {
      const CurrentType = listType
      const items = [...listItems]
      elements.push(
        <CurrentType key={key += 1} className={`kn-md-list ${CurrentType}`}>
          {items}
        </CurrentType>
      )
      listItems = []
      listType = null
    }
  }

  const inlineFormat = (str: string) => {
    const parts: (JSX.Element | string)[] = []
    const re = /\*\*(.+?)\*\*|__(.+?)__|`(.+?)`/g
    let last = 0
    let match: RegExpExecArray | null
    let keyIdx = 0
    while ((match = re.exec(str)) !== null) {
      if (match.index > last) {
        parts.push(str.slice(last, match.index))
      }
      if (match[1] || match[2]) {
        parts.push(<strong key={keyIdx += 1} className="kn-bold">{match[1] ?? match[2]}</strong>)
      } else if (match[3]) {
        parts.push(<code key={keyIdx += 1} className="kn-code">{match[3]}</code>)
      }
      last = match.index + match[0].length
    }
    if (last < str.length) {
      parts.push(str.slice(last))
    }
    return parts.length ? parts : [str]
  }

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i]
    const trimmed = line.trim()

    if (!trimmed) {
      flushList()
      elements.push(<div key={key += 1} className="kn-md-para-spacing" />)
      continue
    }

    const headingMatch = line.match(/^(#{1,6})\s+(.+)$/)
    if (headingMatch) {
      flushList()
      const level = headingMatch[1].length
      elements.push(
        <div key={key += 1} className={`kn-md-heading h${level}`}>
          {inlineFormat(headingMatch[2])}
        </div>
      )
      continue
    }

    const bulletMatch = line.match(/^[-*+]\s+(.+)$/)
    if (bulletMatch) {
      if (listType !== 'ul') {
        flushList()
        listType = 'ul'
      }
      listItems.push(<li key={listItems.length}>{inlineFormat(bulletMatch[1])}</li>)
      continue
    }

    const numberedMatch = line.match(/^(\d+)[.)]\s+(.+)$/)
    if (numberedMatch) {
      if (listType !== 'ol') {
        flushList()
        listType = 'ol'
      }
      listItems.push(<li key={listItems.length} value={parseInt(numberedMatch[1], 10)}>{inlineFormat(numberedMatch[2])}</li>)
      continue
    }

    flushList()
    elements.push(
      <p key={key += 1} className="kn-md-p">
        {inlineFormat(line)}
      </p>
    )
  }

  flushList()
  return <div className="kn-markdown-body">{elements}</div>
}

export default function KnowledgePage() {
  const { authFetch } = useAuth()
  const { theme } = useTheme()
  const isDark = theme === 'dark'

  const [docs, setDocs] = useState<DocRecord[]>([])
  const [activeModalDoc, setActiveModalDoc] = useState<DocRecord | null>(null)
  const [modalContent, setModalContent] = useState<string>('')
  const [modalLoading, setModalLoading] = useState(false)
  const [modalError, setModalError] = useState<string | null>(null)
  const [searchTerm, setSearchTerm] = useState('')
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [uploadMsg, setUploadMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  async function loadDocs() {
    setLoading(true)
    try {
      const response = await authFetch('/api/ld/docs')
      if (!response.ok) throw new Error('Không tải được danh sách tài liệu.')
      const data = await response.json()
      const nextDocs = normaliseDocs(data)
      setDocs(nextDocs)
    } catch (error: any) {
      setDocs([])
      setUploadMsg({ type: 'err', text: error?.message || 'Không tải được dữ liệu tri thức.' })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadDocs()
  }, [])

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return
    setUploading(true)
    setUploadMsg(null)

    try {
      for (const file of Array.from(files)) {
        const formData = new FormData()
        formData.append('file', file)
        const response = await authFetch('/api/ld/docs/upload', { method: 'POST', body: formData })
        if (!response.ok) throw new Error(`Upload thất bại: ${file.name}`)
      }
      setUploadMsg({ type: 'ok', text: `Đã upload ${files.length} tài liệu thành công.` })
      await loadDocs()
    } catch (error: any) {
      setUploadMsg({ type: 'err', text: error?.message || 'Upload thất bại.' })
    } finally {
      setUploading(false)
    }
  }

  async function openDocModal(doc: DocRecord) {
    setActiveModalDoc(doc)
    setModalContent('')
    setModalError(null)
    setModalLoading(true)
    try {
      const res = await authFetch(`/api/ld/docs/${encodeURIComponent(doc.name)}/content`)
      if (!res.ok) throw new Error('Không tải được nội dung tài liệu.')
      const data = await res.json()
      setModalContent(data.content || '')
    } catch (err: any) {
      setModalError(err?.message || 'Lỗi khi tải nội dung.')
    } finally {
      setModalLoading(false)
    }
  }

  const filteredDocs = useMemo(() => {
    return docs.filter((doc) => {
      const term = searchTerm.toLowerCase().trim()
      if (!term) return true
      return (
        doc.name.toLowerCase().includes(term) ||
        (doc.summary && doc.summary.toLowerCase().includes(term))
      )
    })
  }, [docs, searchTerm])

  const stats = useMemo(() => {
    const totalSize = docs.reduce((sum, doc) => sum + doc.size, 0)
    const formats = new Set(docs.map((doc) => doc.type.toUpperCase()))
    const latest = docs
      .map((doc) => doc.uploaded_at)
      .sort((left, right) => new Date(right).getTime() - new Date(left).getTime())[0]

    return {
      totalDocs: docs.length,
      totalSize,
      formats: formats.size,
      latest: latest ? fmtDate(latest) : 'Chưa có',
    }
  }, [docs])

  function getTypeIcon(type: string) {
    if (type === 'pdf') return 'PDF'
    if (type === 'txt' || type === 'md') return 'TXT'
    if (type === 'docx' || type === 'doc') return 'DOC'
    if (type === 'xlsx' || type === 'xls') return 'XLS'
    return 'FILE'
  }

  return (
    <div className={`kn-layout ${theme}`}>
      <ParticleBackground densityMultiplier={0.78} maxParticles={42} connectionDistance={118} speedMultiplier={0.82} zIndex={0} />
      <div className="kn-layer">
        <SubNav items={DASHBOARD_SUBNAV} />
        <main className="kn-main">
          <div className="kn-main-inner">
            <section className="kn-hero">
              <div className="kn-hero-copy">
                <span className="kn-eyebrow">Knowledge base</span>
                <h1 className="kn-title">Kho tri thức dùng cho AI nội bộ</h1>
                <p className="kn-subtitle">Tài liệu ở đây là nguồn tham chiếu để AI trả lời đúng ngữ cảnh annotation, lane rules và hướng dẫn QA.</p>
              </div>
              <div className="kn-hero-actions">
                <button className="kn-upload-btn" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
                  {uploading ? 'Đang upload...' : 'Upload tài liệu'}
                </button>
              </div>
            </section>

            {uploadMsg ? (
              <div className={`upload-msg ${uploadMsg.type}`}>
                <span>{uploadMsg.text}</span>
                <button onClick={() => setUploadMsg(null)}>✕</button>
              </div>
            ) : null}

            <section className="kn-grid">
              {/* Left Column: Upload Area & Statistics Overview */}
              <div className="kn-column upload-panel">
                <div
                  className={`drop-zone ${dragOver ? 'drag-over' : ''}`}
                  onDragOver={(event) => {
                    event.preventDefault()
                    setDragOver(true)
                  }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={(event) => {
                    event.preventDefault()
                    setDragOver(false)
                    void handleFiles(event.dataTransfer.files)
                  }}
                  onClick={() => fileInputRef.current?.click()}
                >
                  <div className="drop-icon">KB</div>
                  <p className="drop-title">Kéo thả tài liệu hoặc click chọn file</p>
                  <p className="drop-sub">Hỗ trợ PDF, TXT, MD, DOCX, XLSX.</p>
                </div>

                <div className="stats-panel-cards">
                  <div className="stats-panel-grid">
                    <article className="kn-metric-card">
                      <span>Tài liệu</span>
                      <strong>{stats.totalDocs}</strong>
                      <small>Tổng số file đang dùng</small>
                    </article>
                    <article className="kn-metric-card">
                      <span>Dung lượng</span>
                      <strong>{fmtSize(stats.totalSize)}</strong>
                      <small>Tổng dữ liệu đã nạp</small>
                    </article>
                    <article className="kn-metric-card">
                      <span>Định dạng</span>
                      <strong>{stats.formats}</strong>
                      <small>Số loại file đang hỗ trợ</small>
                    </article>
                    <article className="kn-metric-card">
                      <span>Cập nhật</span>
                      <strong>{stats.latest}</strong>
                      <small>Mốc làm mới gần nhất</small>
                    </article>
                  </div>
                </div>
              </div>

              {/* Right Column: Documents feed displaying inline summaries */}
              <div className="doc-list-wrap">
                <div className="doc-list-header">
                  <div>
                    <strong>Tài liệu tri thức</strong>
                    <small>{filteredDocs.length} file sẵn sàng cho AI</small>
                  </div>
                  <button className="refresh-btn" onClick={() => void loadDocs()} disabled={loading}>
                    {loading ? 'Đang tải...' : 'Làm mới'}
                  </button>
                </div>

                <div className="doc-search-box">
                  <input
                    type="text"
                    placeholder="Tìm kiếm tài liệu..."
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                    className="doc-search-input"
                  />
                  {searchTerm && (
                    <button className="search-clear-btn" onClick={() => setSearchTerm('')}>
                      ✕
                    </button>
                  )}
                </div>

                {loading ? (
                  <div className="loading-state">Đang tải dữ liệu tri thức...</div>
                ) : filteredDocs.length === 0 ? (
                  <div className="empty-state">
                    {searchTerm ? 'Không tìm thấy tài liệu phù hợp.' : 'Chưa có tài liệu nào trong kho tri thức.'}
                  </div>
                ) : (
                  <div className="doc-feed-grid">
                    {filteredDocs.map((doc) => (
                      <div
                        key={doc.id}
                        className={`doc-feed-card doc-type-${doc.type.toLowerCase()}`}
                        onClick={() => void openDocModal(doc)}
                        style={{ cursor: 'pointer' }}
                      >
                        <div className="doc-feed-header">
                          <div className="doc-icon">{getTypeIcon(doc.type)}</div>
                          <div className="doc-feed-title-wrap">
                            <h3 className="doc-feed-name" title={doc.name}>{doc.name}</h3>
                            <div className="doc-meta">
                              <span className="doc-type">{doc.type.toUpperCase()}</span>
                              <span>{fmtSize(doc.size)}</span>
                              <span>{fmtDate(doc.uploaded_at)}</span>
                            </div>
                          </div>
                          <span className="doc-click-hint">Click để xem nội dung →</span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </section>

            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".pdf,.txt,.md,.docx,.xlsx"
              style={{ display: 'none' }}
              onChange={(event) => void handleFiles(event.target.files)}
            />

            {activeModalDoc && (
              <div className="kn-modal-backdrop" onClick={() => { setActiveModalDoc(null); setModalContent(''); setModalError(null) }}>
                <div className="kn-modal-container" onClick={(e) => e.stopPropagation()}>
                  <div className="kn-modal-header">
                    <div className="kn-modal-file-info">
                      <div className={`doc-icon doc-type-${activeModalDoc.type.toLowerCase()}`}>{getTypeIcon(activeModalDoc.type)}</div>
                      <div className="kn-modal-title-wrap">
                        <h3 className="kn-modal-title" title={activeModalDoc.name}>{activeModalDoc.name}</h3>
                        <div className="doc-meta">
                          <span className="doc-type">{activeModalDoc.type.toUpperCase()}</span>
                          <span>{fmtSize(activeModalDoc.size)}</span>
                          <span>{fmtDate(activeModalDoc.uploaded_at)}</span>
                        </div>
                      </div>
                    </div>
                    <button className="kn-modal-close-btn" onClick={() => { setActiveModalDoc(null); setModalContent(''); setModalError(null) }}>✕</button>
                  </div>

                  <div className="kn-modal-body">
                    {modalLoading ? (
                      <div className="kn-modal-loading">
                        <div className="kn-modal-spinner" />
                        <span>Đang tải nội dung tài liệu...</span>
                      </div>
                    ) : modalError ? (
                      <div className="kn-modal-error">
                        <span>⚠ {modalError}</span>
                      </div>
                    ) : (
                      <div className="kn-modal-text-content">
                        {modalContent ? renderDocMarkdown(modalContent) : <p className="empty-text">Tài liệu không có nội dung văn bản.</p>}
                      </div>
                    )}
                  </div>

                  <div className="kn-modal-footer">
                    {!modalLoading && !modalError && modalContent && (
                      <button
                        className="kn-modal-copy-btn"
                        onClick={(e) => {
                          e.stopPropagation()
                          const btn = e.currentTarget
                          void navigator.clipboard.writeText(modalContent)
                          btn.innerText = 'Đã sao chép!'
                          setTimeout(() => { btn.innerText = 'Copy nội dung' }, 2000)
                        }}
                      >
                        Copy nội dung
                      </button>
                    )}
                    <button className="kn-modal-close-action" onClick={() => { setActiveModalDoc(null); setModalContent(''); setModalError(null) }}>Đóng</button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  )
}
