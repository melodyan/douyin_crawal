<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

type Page = 'overview' | 'tasks' | 'videos' | 'settings'
type TaskMode = 'video' | 'collection' | 'author'
type TaskModule = 'download' | 'transcribe' | 'comments' | 'report'
type TaskState = '运行中' | '已完成' | '待处理' | '失败' | '已取消'
type TranscriptState = '未转写' | '转写中' | '已转写'
type CommentsState = '未采集' | '采集中' | '已采集'
type StepStatus = 'done' | 'failed' | 'pending' | 'running' | 'cancelled'
interface Task {
  id: string
  title: string
  mode: string
  module: string
  input: string
  state: TaskState
  progress: number
  createdAt: string
  collectedAt: string
  publishedAt: string
  author: string
  transcript: string
  likes: number
  favorites: number
  shares: number
  transcriptState: TranscriptState
  commentsState: CommentsState
  inReadingList: boolean
  videoStatus: StepStatus
  commentsStatus: StepStatus
  transcriptStatus: StepStatus
  reportStatus: StepStatus
}

interface CollectedVideo {
  platform: string
  video_id: string
  url: string
  title: string
  description: string
  author_id: string
  author_name: string
  published_at: string
  likes: number
  favorites: number
  shares: number
  hashtags: string[]
  cover_url: string
  duration_seconds: number
  content_type: string
  local_media_path: string
  metadata_status: string
  metadata_error: string
  transcript_status: string
  transcript: string
  transcript_error: string
  collected_at: string
  updated_at: string
}

interface VideoComment { comment_id: string; text: string; likes: number; author: string; parent_id: string; reply_to_id: string; author_id: string; published_at: string }

const storedPage = localStorage.getItem('douyin-current-page')
const page = ref<Page>(storedPage === 'overview' || storedPage === 'tasks' || storedPage === 'videos' || storedPage === 'settings' ? storedPage : 'tasks')
const toast = ref('')
const showCreate = ref(false)
const activeMode = ref<TaskMode>('video')
const activeModules = ref<TaskModule[]>(['download', 'transcribe', 'comments', 'report'])
const taskName = ref('')
const sourceUrl = ref('')
const tempConfig = ref(false)
const selectedSettings = ref(['crawl.max_comments_per_video', 'inputs.max_videos_per_profile'])
const settingsGroup = ref('inputs')
const revealKey = ref(false)
const secretConfigured = ref(false)
const taskFilter = ref('全部任务')
const appliedTaskFilter = ref('全部任务')
const activeMenuTaskId = ref('')
const deleteTarget = ref<Task | null>(null)
const videos = ref<CollectedVideo[]>([])
const videoTotal = ref(0)
const videoSearch = reactive({ author: '', title: '', publishedFrom: localDate(), publishedTo: localDate() })
const selectedVideo = ref<CollectedVideo | null>(null)
const selectedVideoComments = ref<VideoComment[]>([])
const videoCommentsLoading = ref(false)
function localDate(offsetDays = 0) { const date = new Date(); date.setDate(date.getDate() + offsetDays); return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}` }
const filters = reactive({ videoName: '', collectedFrom: localDate(), collectedTo: localDate() })
const appliedFilters = reactive({ ...filters })
const settingsSearch = ref('')
const storedFontOffset = Number(localStorage.getItem('douyin-font-offset') ?? 2)
const fontOffset = ref(Number.isFinite(storedFontOffset) ? Math.min(6, Math.max(0, storedFontOffset)) : 2)

const config = reactive<Record<string, string | number | boolean>>({
  platform: 'douyin',
  'inputs.urls': '', 'inputs.urls_file': '', 'inputs.max_videos_per_profile': '', 'inputs.max_videos_per_collection': '',
  'browser.headless': false, 'browser.profile_dir': 'work/browser', 'browser.navigation_timeout_seconds': 30, 'browser.manual_wait_seconds': 0,
  'crawl.delay_seconds': 2, 'crawl.max_retries': 3, 'crawl.retry_backoff_seconds': 5, 'crawl.max_comments_per_video': '', 'crawl.no_new_content_scrolls': 3, 'crawl.resume': true, 'crawl.refresh_completed': false,
  'audio.ffmpeg_path': 'ffmpeg', 'audio.mp3_bitrate_kbps': 64, 'audio.download_timeout_seconds': 120, 'audio.keep_temporary_files': false,
  'download.output_dir': 'outputs/downloads',
  'asr.api_key': '', 'asr.base_url': 'https://api.xiaomimimo.com/v1', 'asr.model': 'mimo-v2.5-asr', 'asr.language': 'auto', 'asr.chunk_seconds': 180, 'asr.overlap_seconds': 2, 'asr.timeout_seconds': 120, 'asr.max_retries': 3, 'asr.output_dir': 'outputs/transcripts',
  'report.output_dir': 'outputs/reports', 'report.top_comments': '', 'report.min_comment_chars': 50, 'report.include_comment_author': false, 'report.write_partial_results': true,
  'runtime.data_dir': '.', 'runtime.state_db': 'work/state.sqlite3', 'runtime.log_file': 'work/run.log', 'runtime.log_level': 'INFO',
})
const defaultConfig = { ...config }

const groups = [
  { id: 'inputs', label: '输入与平台', caption: '来源链接与采集范围', icon: '↗' },
  { id: 'browser', label: '浏览器', caption: '登录与页面访问', icon: '◉' },
  { id: 'crawl', label: '采集策略', caption: '速度、重试与续跑', icon: '⌁' },
  { id: 'media', label: '媒体与下载', caption: 'FFmpeg 与文件保存', icon: '▣' },
  { id: 'asr', label: '语音转写', caption: 'MiMo 与音频分段', icon: '◖' },
  { id: 'report', label: '报告', caption: '输出内容与筛选', icon: '▤' },
  { id: 'runtime', label: '运行与存储', caption: '状态库与日志', icon: '⚙' },
]

interface Setting { key: string; label: string; description: string; kind?: 'number' | 'toggle' | 'select' | 'secret' | 'textarea'; options?: string[]; unit?: string }
const definitions: Record<string, Setting[]> = {
  inputs: [
    { key: 'platform', label: '采集平台', description: '当前版本支持抖音。', kind: 'select', options: ['douyin', 'xiaohongshu', 'bilibili'] },
    { key: 'inputs.urls', label: '默认链接', description: '每行填写一个视频、合集或作者主页链接。', kind: 'textarea' },
    { key: 'inputs.urls_file', label: '链接文件', description: '可选的批量链接文本文件路径。' },
    { key: 'inputs.max_videos_per_profile', label: '作者作品上限', description: '留空表示尽量遍历全部公开视频。', kind: 'number', unit: '条' },
    { key: 'inputs.max_videos_per_collection', label: '合集作品上限', description: '留空表示处理合集中的全部作品。', kind: 'number', unit: '条' },
  ],
  browser: [
    { key: 'browser.headless', label: '后台运行浏览器', description: '关闭后会显示浏览器窗口，便于登录或完成验证。', kind: 'toggle' },
    { key: 'browser.profile_dir', label: '浏览器会话目录', description: '保存可复用的登录状态。' },
    { key: 'browser.navigation_timeout_seconds', label: '页面加载超时', description: '单个页面最多等待时间。', kind: 'number', unit: '秒' },
    { key: 'browser.manual_wait_seconds', label: '人工验证等待', description: '设为 0 表示持续等待。', kind: 'number', unit: '秒' },
  ],
  crawl: [
    { key: 'crawl.delay_seconds', label: '采集间隔', description: '两次页面采集操作之间的间隔。', kind: 'number', unit: '秒' },
    { key: 'crawl.max_retries', label: '失败重试次数', description: '页面加载或临时采集失败后的重试上限。', kind: 'number', unit: '次' },
    { key: 'crawl.retry_backoff_seconds', label: '首次重试等待', description: '后续重试会逐步延长等待时间。', kind: 'number', unit: '秒' },
    { key: 'crawl.max_comments_per_video', label: '单视频评论上限', description: '留空表示尽量采集全部一级评论及回复。', kind: 'number', unit: '条' },
    { key: 'crawl.no_new_content_scrolls', label: '无新内容滚动次数', description: '连续没有新内容时停止翻页。', kind: 'number', unit: '次' },
    { key: 'crawl.resume', label: '断点续跑', description: '跳过状态库中已完成的处理步骤。', kind: 'toggle' },
    { key: 'crawl.refresh_completed', label: '重新处理已完成作品', description: '重新采集和转写已有作品。', kind: 'toggle' },
  ],
  media: [
    { key: 'audio.ffmpeg_path', label: 'FFmpeg 程序位置', description: '已加入系统路径时填写 ffmpeg。' },
    { key: 'audio.mp3_bitrate_kbps', label: '音频码率', description: '临时 MP3 音频码率。', kind: 'number', unit: 'kbps' },
    { key: 'audio.download_timeout_seconds', label: '媒体下载超时', description: '获取单条视频媒体的最长等待时间。', kind: 'number', unit: '秒' },
    { key: 'audio.keep_temporary_files', label: '保留临时视频', description: '转写完成后是否保留下载的视频文件。', kind: 'toggle' },
    { key: 'download.output_dir', label: '下载文件目录', description: '单独下载的视频或图文保存位置。' },
  ],
  asr: [
    { key: 'asr.api_key', label: 'MiMo API Key', description: '仅保存在本机配置中。', kind: 'secret' },
    { key: 'asr.base_url', label: 'API 地址', description: 'MiMo 语音识别服务地址。' },
    { key: 'asr.model', label: '识别模型', description: '用于视频音频转写的模型名称。' },
    { key: 'asr.language', label: '识别语言', description: '选择自动识别或指定语言。', kind: 'select', options: ['auto', 'zh', 'en'] },
    { key: 'asr.chunk_seconds', label: '音频分段长度', description: '长音频按此时长切片。', kind: 'number', unit: '秒' },
    { key: 'asr.overlap_seconds', label: '分段重叠时长', description: '减少片段边界漏字。', kind: 'number', unit: '秒' },
    { key: 'asr.timeout_seconds', label: '请求超时', description: '单次转写请求最长等待时间。', kind: 'number', unit: '秒' },
    { key: 'asr.max_retries', label: '请求重试次数', description: '临时失败后的重试上限。', kind: 'number', unit: '次' },
    { key: 'asr.output_dir', label: '转写文本目录', description: '单独转写任务的文本保存位置。' },
  ],
  report: [
    { key: 'report.output_dir', label: '报告目录', description: 'Markdown 报告的保存位置。' },
    { key: 'report.top_comments', label: '报告评论数量', description: '留空表示写入全部已采集评论。', kind: 'number', unit: '条' },
    { key: 'report.min_comment_chars', label: '评论最少字数', description: '低于此字数的评论不写入报告。', kind: 'number', unit: '字' },
    { key: 'report.include_comment_author', label: '报告显示评论者', description: '是否在报告中显示评论者昵称。', kind: 'toggle' },
    { key: 'report.write_partial_results', label: '保留部分结果', description: '部分步骤失败时仍生成并标注报告。', kind: 'toggle' },
  ],
  runtime: [
    { key: 'runtime.data_dir', label: '数据根目录', description: '状态库、日志、浏览器会话和输出文件的统一根目录。' },
    { key: 'runtime.state_db', label: '状态数据库', description: '保存采集状态、评论和转写结果。' },
    { key: 'runtime.log_file', label: '日志文件', description: '记录任务进度与失败原因。' },
    { key: 'runtime.log_level', label: '日志详细程度', description: '控制日志记录级别。', kind: 'select', options: ['DEBUG', 'INFO', 'WARNING', 'ERROR'] },
  ],
}

const tasks = ref<Task[]>([])
const readingIds = new Set<string>(JSON.parse(localStorage.getItem('douyin-reading-tasks') || '[]'))

const API_BASE = (import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000').replace(/\/$/, '')
async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }, ...options })
  const payload = await response.json()
  if (!response.ok) throw new Error(payload.detail || payload.error?.message || `请求失败 (${response.status})`)
  return payload.data as T
}
function taskFromApi(item: any): Task {
  const state: TaskState = item.state === 'done' ? '已完成' : item.state === 'failed' ? '失败' : item.state === 'cancelled' ? '已取消' : item.state === 'running' ? '运行中' : '待处理'
  const steps = item.steps || {}
  const input = (item.urls || []).join('\n')
  const displayMode = input.includes('/collection/') ? '短视频合集' : input.includes('/user/') ? '指定作者' : '单个短视频'
  return { id: item.id, title: item.title || item.mode || '采集任务', mode: displayMode, module: item.mode === 'run' ? '完整处理' : '仅采集', input, state, progress: item.progress || 0, createdAt: item.created_at || '', collectedAt: (item.created_at || '').slice(0, 10), publishedAt: '', author: '采集任务', transcript: item.error || '', likes: 0, favorites: 0, shares: 0, transcriptState: steps.transcribe === 'done' ? '已转写' : steps.transcribe === 'running' ? '转写中' : '未转写', commentsState: steps.comments === 'done' ? '已采集' : steps.comments === 'running' ? '采集中' : '未采集', inReadingList: readingIds.has(item.id), videoStatus: steps.download || steps.collect || 'pending', commentsStatus: steps.comments || 'pending', transcriptStatus: steps.transcribe || 'pending', reportStatus: steps.report || 'pending' }
}
async function refreshTasks() {
  try { const result = await api<{ items: any[] }>('/api/tasks'); tasks.value = result.items.map(taskFromApi) } catch (error) { notify(`后端未连接：${(error as Error).message}`) }
}
async function refreshVideos() {
  const query = new URLSearchParams()
  if (videoSearch.author.trim()) query.set('author', videoSearch.author.trim())
  if (videoSearch.title.trim()) query.set('title', videoSearch.title.trim())
  if (videoSearch.publishedFrom) query.set('published_from', videoSearch.publishedFrom)
  if (videoSearch.publishedTo) query.set('published_to', videoSearch.publishedTo)
  try {
    const result = await api<{ items: CollectedVideo[]; total: number }>(`/api/videos?${query.toString()}`)
    videos.value = result.items
    videoTotal.value = result.total
  } catch (error) { notify(`视频数据读取失败：${(error as Error).message}`) }
}
async function openVideoDetail(video: CollectedVideo) {
  selectedVideo.value = video
  selectedVideoComments.value = []
  videoCommentsLoading.value = true
  try {
    const result = await api<{ items: VideoComment[] }>(`/api/videos/${encodeURIComponent(video.platform)}/${encodeURIComponent(video.video_id)}/comments?limit=500`)
    selectedVideoComments.value = result.items
  } catch (error) { notify(`评论读取失败：${(error as Error).message}`) }
  finally { videoCommentsLoading.value = false }
}
function resetVideoSearch() { videoSearch.author = ''; videoSearch.title = ''; videoSearch.publishedFrom = localDate(); videoSearch.publishedTo = localDate(); refreshVideos() }
function setVideoDateRange(range: 'today' | 'yesterday' | 'week' | 'month') {
  const endOffset = range === 'yesterday' ? -1 : 0
  const startOffset = range === 'week' ? -6 : range === 'month' ? -29 : endOffset
  videoSearch.publishedFrom = localDate(startOffset)
  videoSearch.publishedTo = localDate(endOffset)
  refreshVideos()
}
function formatCount(value: number) { return new Intl.NumberFormat('zh-CN').format(value || 0) }
function formatDateTime(value: string) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—' }
function processingLabel(video: CollectedVideo) {
  if (video.metadata_status === 'failed') return '采集失败'
  if (video.transcript_status === 'complete') return '已转写'
  if (video.local_media_path) return '已下载'
  return video.metadata_status === 'complete' ? '已采集' : '部分数据'
}

const modeOptions = [
  { id: 'video' as TaskMode, icon: '▶', title: '单个短视频', desc: '粘贴一个作品链接' },
  { id: 'collection' as TaskMode, icon: '▤', title: '短视频合集', desc: '按合集顺序处理作品' },
  { id: 'author' as TaskMode, icon: '◎', title: '指定作者', desc: '采集作者主页作品' },
]
const moduleOptions = [
  { id: 'download' as TaskModule, title: '下载视频', desc: '保存视频或图文媒体文件' },
  { id: 'transcribe' as TaskModule, title: '转写文字', desc: '调用 MiMo 生成转写正文' },
  { id: 'comments' as TaskModule, title: '拉取评论', desc: '采集一级评论及其回复' },
  { id: 'report' as TaskModule, title: '生成报告', desc: '输出作品、主页或合集报告' },
]
const transcribeOnly = computed(() => activeModules.value.length === 1 && activeModules.value[0] === 'transcribe')
const visibleTasks = computed(() => tasks.value.filter((task) => {
  const includes = (value: string, query: string) => !query.trim() || value.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())
  const inDate = (value: string, from: string, to: string) => (!from || value >= from) && (!to || value <= to)
  return includes(task.title, appliedFilters.videoName) && (appliedTaskFilter.value === '全部任务' || task.state === appliedTaskFilter.value)
    && inDate(task.collectedAt, appliedFilters.collectedFrom, appliedFilters.collectedTo)
}))
const activeGroup = computed(() => groups.find((group) => group.id === settingsGroup.value)!)
const visibleSettings = computed(() => (definitions[settingsGroup.value] || []).filter((item) => `${item.label}${item.description}${item.key}`.toLowerCase().includes(settingsSearch.value.toLowerCase())))
const settingCount = computed(() => Object.values(definitions).reduce((sum, items) => sum + items.length, 0))

function notify(message: string) {
  toast.value = message
  window.setTimeout(() => { if (toast.value === message) toast.value = '' }, 2400)
}
function createTask() {
  if (!taskName.value.trim()) { notify('请填写采集任务名称'); return }
  if (!sourceUrl.value.trim()) { notify(transcribeOnly.value ? '请填写本地视频绝对路径' : '请先填写抖音链接'); return }
  const mode = modeOptions.find((item) => item.id === activeMode.value)!
  if (!activeModules.value.length) { notify('至少选择一个处理模块'); return }
  if (transcribeOnly.value && !/^(?:[A-Za-z]:[\\/]|\/)/.test(sourceUrl.value.trim())) { notify('本地视频请填写绝对路径'); return }
  if (activeModules.value.includes('transcribe') && !transcribeOnly.value && !activeModules.value.includes('download')) { notify('转写与其他模块同时使用时，请同时选择“下载视频”'); return }
  const options: Record<string, unknown> = {
    title: taskName.value.trim(),
    download_video: activeModules.value.includes('download'),
    transcribe_text: activeModules.value.includes('transcribe'),
    include_comments: activeModules.value.includes('comments'),
    generate_report: activeModules.value.includes('report'),
    force_reprocess: config['crawl.refresh_completed'],
  }
  if (transcribeOnly.value) options.local_video_path = sourceUrl.value.trim()
  if (tempConfig.value) for (const key of selectedSettings.value) options[key === 'crawl.refresh_completed' ? 'force_reprocess' : key.split('.').slice(-1)[0]] = config[key] === '' ? null : config[key]
  const taskMode = activeModules.value.some((item) => item !== 'comments') ? 'run' : 'collect'
  const createdName = taskName.value.trim()
  api('/api/tasks', { method: 'POST', body: JSON.stringify({ mode: taskMode, urls: transcribeOnly.value ? [] : [sourceUrl.value.trim()], options }) }).then(() => { showCreate.value = false; taskName.value = ''; sourceUrl.value = ''; notify(`已创建“${createdName || mode.title}”任务`); refreshTasks() }).catch((error) => notify(`创建失败：${error.message}`))
}
function toggleModule(id: TaskModule) {
  if (activeModules.value.includes(id)) {
    activeModules.value = activeModules.value.filter((module) => module !== id)
  } else {
    activeModules.value = [...activeModules.value, id]
  }
}
function transcribe(task: Task) {
  if (task.transcriptState === '转写中') return
  activeMenuTaskId.value = ''
  api('/api/tasks', { method: 'POST', body: JSON.stringify({ mode: 'run', urls: task.input.split('\n').filter(Boolean), options: { title: `${task.title} · 重新转写`, download_video: true, transcribe_text: true, include_comments: false, generate_report: false, force_reprocess: true } }) }).then(() => { notify(`“${task.title}”已加入转写队列`); refreshTasks() }).catch((error) => notify(`提交失败：${error.message}`))
}
function pullComments(task: Task) {
  if (task.commentsState === '采集中') return
  activeMenuTaskId.value = ''
  api('/api/tasks', { method: 'POST', body: JSON.stringify({ mode: 'collect', urls: task.input.split('\n').filter(Boolean), options: { title: `${task.title} · 更新评论`, download_video: false, transcribe_text: false, include_comments: true, generate_report: false, force_reprocess: true } }) }).then(() => { notify(`“${task.title}”已加入评论采集队列`); refreshTasks() }).catch((error) => notify(`提交失败：${error.message}`))
}
function retryTask(task: Task) { activeMenuTaskId.value = ''; api(`/api/tasks/${task.id}/retry`, { method: 'POST' }).then(() => { notify('已创建重试任务'); refreshTasks() }).catch((error) => notify(`重试失败：${error.message}`)) }
function cancelTask(task: Task) { activeMenuTaskId.value = ''; api(`/api/tasks/${task.id}/cancel`, { method: 'POST' }).then(() => { notify('已请求取消任务'); refreshTasks() }).catch((error) => notify(`取消失败：${error.message}`)) }
function statusLabel(label: string, status: StepStatus) {
  return `${label}${status === 'done' ? '已完成' : status === 'failed' ? '失败' : status === 'cancelled' ? '已取消' : status === 'running' ? '处理中' : '未处理'}`
}
function toggleReading(task: Task) {
  task.inReadingList = !task.inReadingList
  if (task.inReadingList) readingIds.add(task.id); else readingIds.delete(task.id)
  localStorage.setItem('douyin-reading-tasks', JSON.stringify([...readingIds]))
  activeMenuTaskId.value = ''
  notify(task.inReadingList ? '已加入阅读' : '已从阅读列表移除')
}
function confirmDelete() {
  if (!deleteTarget.value) return
  const title = deleteTarget.value.title
  const id = deleteTarget.value.id
  api(`/api/tasks/${id}`, { method: 'DELETE' }).then(() => { tasks.value = tasks.value.filter((task) => task.id !== id); deleteTarget.value = null; activeMenuTaskId.value = ''; notify(`已删除“${title}”`) }).catch((error) => notify(`删除失败：${error.message}`))
}
function closeMenus(event: MouseEvent) {
  if (!(event.target as HTMLElement).closest('.row-actions')) activeMenuTaskId.value = ''
}
let eventSource: EventSource | null = null
onMounted(() => { document.documentElement.style.setProperty('--font-offset', `${fontOffset.value}px`); document.addEventListener('click', closeMenus); refreshTasks(); refreshVideos(); loadSettings(); eventSource = new EventSource(`${API_BASE}/api/tasks/events`); eventSource.addEventListener('tasks', (event) => { tasks.value = JSON.parse((event as MessageEvent).data).map(taskFromApi) }) })
onBeforeUnmount(() => { document.removeEventListener('click', closeMenus); eventSource?.close() })
function clearFilters() {
  taskFilter.value = '全部任务'
  appliedTaskFilter.value = '全部任务'
  Object.assign(filters, { videoName: '', collectedFrom: localDate(), collectedTo: localDate() })
  Object.assign(appliedFilters, filters)
}
function setDateRange(range: 'today' | 'yesterday' | 'week' | 'month') {
  const endOffset = range === 'yesterday' ? -1 : 0
  const startOffset = range === 'week' ? -6 : range === 'month' ? -29 : endOffset
  filters.collectedFrom = localDate(startOffset)
  filters.collectedTo = localDate(endOffset)
  applyFilters()
}
function applyFilters() {
  appliedTaskFilter.value = taskFilter.value
  Object.assign(appliedFilters, filters)
  notify(`查询完成，找到 ${visibleTasks.value.length} 条任务`)
}
function changeFontSize(delta: number) { fontOffset.value = Math.min(6, Math.max(0, fontOffset.value + delta)); document.documentElement.style.setProperty('--font-offset', `${fontOffset.value}px`); localStorage.setItem('douyin-font-offset', String(fontOffset.value)) }
function saveSettings() {
  api('/api/config', { method: 'PUT', body: JSON.stringify({ values: { ...config } }) }).then(() => { secretConfigured.value ||= Boolean(config['asr.api_key']); config['asr.api_key'] = ''; notify('配置已保存到后端') }).catch((error) => notify(`配置保存失败：${error.message}`))
}
function resetSettings() {
  Object.assign(config, defaultConfig)
  notify('已载入默认值，点击保存后生效')
}
async function loadSettings() { try { const result = await api<{ values: Record<string, string | number | boolean>; secrets: Record<string, boolean> }>('/api/config'); Object.assign(config, result.values); secretConfigured.value = result.secrets['asr.api_key']; config['asr.api_key'] = '' } catch (error) { notify(`配置读取失败：${(error as Error).message}`) } }
function stringValue(key: string) { return String(config[key] ?? '') }
function setStringValue(key: string, event: Event) { config[key] = (event.target as HTMLInputElement).value }
function valueFor(key: string) { return config[key] }
watch(() => settingsGroup.value, () => { settingsSearch.value = '' })
watch(page, (value) => localStorage.setItem('douyin-current-page', value))
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand"><div class="brand-mark">采</div><div><strong>采集工作台</strong><span>DOUYIN WORKSPACE</span></div></div>
      <div class="side-label">工作空间</div>
      <button class="nav-item" :class="{ active: page === 'overview' }" @click="page = 'overview'"><span class="nav-icon">▧</span><span>概况预览</span><span v-if="page === 'overview'" class="nav-current"></span></button>
      <button class="nav-item" :class="{ active: page === 'tasks' }" @click="page = 'tasks'"><span class="nav-icon">▦</span><span>任务列表</span><span v-if="page === 'tasks'" class="nav-current"></span></button>
      <button class="nav-item" :class="{ active: page === 'videos' }" @click="page = 'videos'; refreshVideos()"><span class="nav-icon">▶</span><span>采集视频</span><span v-if="page === 'videos'" class="nav-current"></span></button>
      <div class="side-label settings-label">偏好设置</div>
      <button class="nav-item" :class="{ active: page === 'settings' }" @click="page = 'settings'"><span class="nav-icon">⚙</span><span>采集配置</span><span v-if="page === 'settings'" class="nav-current"></span></button>
      <div class="sidebar-bottom"><div class="runtime-card"><div class="runtime-head"><span class="status-dot"></span>本地工作模式</div><p>任务由本机 Python 服务处理<br />状态保存在 SQLite</p><div class="runtime-chip">工作台 <span>v0.2</span></div></div><div class="user-row"><div class="avatar">W</div><div><strong>本机用户</strong><small>个人工作空间</small></div><span class="dots">···</span></div></div>
    </aside>

    <main class="main-area">
      <header class="topbar"><div class="breadcrumb"><span>工作空间</span><b>/</b><strong>{{ page === 'overview' ? '概况预览' : page === 'tasks' ? '任务列表' : page === 'videos' ? '采集视频' : '采集配置' }}</strong></div><div class="top-actions"><div class="font-control" aria-label="全局字体大小"><button aria-label="缩小字体" :disabled="fontOffset === 0" @click="changeFontSize(-1)">A−</button><span>字号 +{{ fontOffset }}px</span><button aria-label="放大字体" :disabled="fontOffset === 6" @click="changeFontSize(1)">A＋</button></div><span class="local-badge"><i></i>本地运行</span><button class="icon-button" aria-label="帮助" @click="notify('任务状态会从本机后端实时更新')">?</button><div class="top-avatar">W</div></div></header>

      <section v-if="page === 'overview'" class="page-content overview-page">
        <div class="page-heading"><div><div class="eyebrow">WORKSPACE <span>·</span> 数据总览</div><h1>概况预览</h1><p>集中查看采集任务与内容处理情况。</p></div><button class="primary-button" @click="page = 'tasks'; showCreate = true"><span>＋</span> 新建任务</button></div>
        <div class="stats-grid"><article class="stat-card"><div class="stat-top"><span>全部任务</span><span class="stat-icon indigo">▦</span></div><strong>{{ tasks.length }}</strong><small>本地任务记录</small></article><article class="stat-card"><div class="stat-top"><span>运行中</span><span class="stat-icon blue">◌</span></div><strong>{{ tasks.filter(t => t.state === '运行中').length }}</strong><small><i class="status-dot"></i>正在处理</small></article><article class="stat-card"><div class="stat-top"><span>已完成</span><span class="stat-icon green">✓</span></div><strong>{{ tasks.filter(t => t.state === '已完成').length }}</strong><small>处理成功</small></article><article class="stat-card"><div class="stat-top"><span>需要关注</span><span class="stat-icon amber">!</span></div><strong>{{ tasks.filter(t => t.state === '失败').length }}</strong><small>查看失败原因</small></article></div>
        <div class="overview-grid"><section class="overview-card"><div class="overview-card-head"><div><h2>最近任务</h2><p>最近加入本机队列的采集任务</p></div><button class="text-link" @click="page = 'tasks'">查看全部 →</button></div><div v-for="task in tasks.slice(0, 4)" :key="task.id" class="overview-video"><div class="overview-play">▶</div><div class="overview-video-copy"><strong>{{ task.title }}</strong><small>{{ task.mode }} · {{ task.collectedAt }}</small></div><span class="overview-metric">{{ task.progress }}%</span></div></section><section class="overview-card overview-status"><div class="overview-card-head"><div><h2>处理概况</h2><p>各处理环节的当前状态</p></div></div><div class="overview-status-row"><span class="status-symbol violet">◖</span><div><strong>视频采集</strong><small>采集视频信息与媒体文件</small></div><b>{{ tasks.length }} 条</b></div><div class="overview-status-row"><span class="status-symbol blue">☷</span><div><strong>评论采集</strong><small>评论与回复采集完成</small></div><b>{{ tasks.filter(t => t.commentsState === '已采集').length }} 条</b></div><div class="overview-status-row"><span class="status-symbol green">≋</span><div><strong>语音转写</strong><small>已生成转写内容</small></div><b>{{ tasks.filter(t => t.transcriptState === '已转写').length }} 条</b></div><button class="overview-link" @click="page = 'tasks'">前往任务列表 <span>→</span></button></section></div>
      </section>

      <section v-else-if="page === 'tasks'" class="page-content">
        <div class="page-heading"><div><div class="eyebrow">WORKSPACE <span>·</span> 任务中心</div><h1>任务列表</h1><p>查看任务进度、处理步骤和错误信息。</p></div><button class="primary-button" @click="showCreate = true"><span>＋</span> 新建任务</button></div>

        <section class="search-panel">
          <div class="search-main">
            <label class="search-box title-search"><span>⌕</span><input v-model="filters.videoName" placeholder="任务名称" @keyup.enter="applyFilters" /></label>
            <div class="filter-field inline-date"><label>采集时间</label><div class="range-inputs"><input v-model="filters.collectedFrom" type="date" aria-label="采集时间开始" /><span>至</span><input v-model="filters.collectedTo" type="date" aria-label="采集时间结束" /></div><div class="date-shortcuts"><button @click="setDateRange('today')">今天</button><button @click="setDateRange('yesterday')">昨天</button><button @click="setDateRange('week')">近一周</button><button @click="setDateRange('month')">近一月</button></div></div>
            <select v-model="taskFilter" aria-label="任务状态"><option>全部任务</option><option>运行中</option><option>已完成</option><option>待处理</option><option>失败</option><option>已取消</option></select>
            <button class="primary-button query-button" @click="applyFilters">查询</button>
          </div>
          <div class="search-foot"><span>可按任务名称、创建时间和状态筛选</span><div><span>已找到 <b>{{ visibleTasks.length }}</b> 条</span><button class="clear-filters" @click="clearFilters">重置条件</button></div></div>
        </section>

        <section v-if="showCreate" class="modal-backdrop" @click.self="showCreate = false"><div class="create-panel" role="dialog" aria-modal="true" aria-labelledby="create-title"><div class="panel-heading"><div><span class="eyebrow">NEW TASK</span><h2 id="create-title">创建采集任务</h2><p>选择内容来源和处理模块。</p></div><button class="close-button" aria-label="关闭" @click="showCreate = false">×</button></div>
          <div class="form-section"><label class="field-label task-name-label" for="task-name">采集任务名称<span>必填</span></label><div class="url-input"><span>✎</span><input id="task-name" v-model="taskName" maxlength="80" placeholder="例如：10月竞品账号采集" /></div><div class="field-hint">任务名称会显示在列表中，并可用于搜索。</div></div>
          <div class="form-section"><div class="section-title"><span class="step-number">1</span><div><strong>{{ transcribeOnly ? '选择本地视频' : '选择采集来源' }}</strong><small>{{ transcribeOnly ? '仅转写时使用本机已有的视频文件' : '告诉我们要从哪里获取内容' }}</small></div></div><div v-if="!transcribeOnly" class="choice-grid"><button v-for="item in modeOptions" :key="item.id" class="choice-card" :class="{ selected: activeMode === item.id }" @click="activeMode = item.id"><span class="choice-icon">{{ item.icon }}</span><span class="choice-copy"><strong>{{ item.title }}</strong><small>{{ item.desc }}</small></span><span class="radio-dot"></span></button></div>
            <label class="field-label" for="source-url">{{ transcribeOnly ? '本地视频绝对路径' : activeMode === 'video' ? '视频链接' : activeMode === 'collection' ? '合集链接' : '作者主页链接' }}<span>必填</span></label><div class="url-input"><span>{{ transcribeOnly ? '▣' : '↗' }}</span><input id="source-url" v-model="sourceUrl" :placeholder="transcribeOnly ? '例如：D:\\videos\\sample.mp4' : '粘贴抖音链接或分享文案'" /><button v-if="sourceUrl" class="clear-input" @click="sourceUrl = ''">×</button></div><div class="field-hint">{{ transcribeOnly ? '必须使用存在且可读的本地文件绝对路径。' : '支持粘贴完整分享文案，系统会识别其中的抖音链接。' }}</div>
          </div>
          <div class="form-section"><div class="section-title"><span class="step-number">2</span><div><strong>选择处理模块</strong><small>四项默认全选，可按任务需要关闭</small></div></div><div class="module-grid"><button v-for="item in moduleOptions" :key="item.id" class="module-card" :class="{ selected: activeModules.includes(item.id) }" @click="toggleModule(item.id)"><span class="module-check">{{ activeModules.includes(item.id) ? '✓' : '' }}</span><strong>{{ item.title }}</strong><small>{{ item.desc }}</small></button></div><div v-if="activeModules.includes('transcribe')" class="transcribe-notice"><span>i</span>{{ transcribeOnly ? '仅转写文字时，需要填写本地视频的绝对路径。' : '转写与其他模块同时使用时，需要同时选择“下载视频”。' }}</div></div>
          <div class="form-section temp-section"><div class="temp-copy"><div class="temp-icon">⚙</div><div><strong>本次任务临时配置</strong><small>默认配置来自“采集配置”页面</small></div></div><button class="switch" :class="{ on: tempConfig }" role="switch" :aria-checked="tempConfig" @click="tempConfig = !tempConfig"><span></span></button><div v-if="tempConfig" class="temp-editor"><p>选择本次要覆盖的配置项，未选择的项目继续使用默认配置。</p><label v-for="option in [{key:'crawl.max_comments_per_video',label:'单视频评论上限'},{key:'inputs.max_videos_per_profile',label:'作者作品上限'},{key:'inputs.max_videos_per_collection',label:'合集作品上限'},{key:'crawl.refresh_completed',label:'重新处理已完成作品'}]" :key="option.key" class="temp-option"><input v-model="selectedSettings" type="checkbox" :value="option.key" /><span>{{ option.label }}</span><b>{{ config[option.key] === '' ? '不限制' : config[option.key] }}</b></label><button class="text-link" @click="page = 'settings'">管理默认配置 <span>↗</span></button></div></div>
          <div class="create-footer"><span><i class="status-dot"></i>任务将在本机队列中处理</span><div><button class="secondary-button" @click="showCreate = false">取消</button><button class="primary-button" @click="createTask">开始任务 <span>→</span></button></div></div>
        </div></section>

        <section class="task-panel"><div class="task-panel-head"><div><h2>最近任务</h2><p>按创建时间倒序排列</p></div><span class="recent-count">{{ visibleTasks.length }} 条记录</span></div>
          <div class="task-table-wrap"><table class="task-table"><thead><tr><th>任务</th><th>类型</th><th>创建时间</th><th>进度</th><th>状态</th><th>错误信息</th><th>处理步骤</th><th>操作</th></tr></thead><tbody><tr v-for="task in visibleTasks" :key="task.id"><td><div class="task-name"><div class="task-type-icon" :class="task.mode === '指定作者' ? 'author' : task.mode === '短视频合集' ? 'collection' : ''">{{ task.mode === '指定作者' ? '◎' : task.mode === '短视频合集' ? '▤' : '▶' }}</div><div class="video-title"><strong>{{ task.title }}</strong><small>{{ task.id }} · {{ task.collectedAt }}</small></div><button class="bookmark-button" :class="{ bookmarked: task.inReadingList }" :aria-label="task.inReadingList ? '取消标记' : '标记任务'" :title="task.inReadingList ? '已标记' : '标记任务'" @click.stop="toggleReading(task)"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.75 4.75h10.5a1 1 0 0 1 1 1v14l-6.25-3.8-6.25 3.8v-14a1 1 0 0 1 1-1Z" /></svg></button></div></td><td><div class="author-cell">{{ task.mode }}</div></td><td class="time-cell">{{ task.createdAt || '—' }}</td><td class="metric-cell">{{ task.progress }}%</td><td>{{ task.state }}</td><td>{{ task.transcript || '—' }}</td><td><div class="step-statuses"><div v-for="step in [{ key: 'video', label: '视频已下载', status: task.videoStatus }, { key: 'transcript', label: '转写已完成', status: task.transcriptStatus }, { key: 'comments', label: '评论已扫描', status: task.commentsStatus }, { key: 'report', label: '报告已生成', status: task.reportStatus }]" :key="step.key" class="step-status" :class="step.status" :title="statusLabel(step.label, step.status)" :aria-label="statusLabel(step.label, step.status)"><span class="step-indicator">{{ step.status === 'done' ? '✓' : step.status === 'failed' ? '×' : step.status === 'running' ? '…' : '·' }}</span><small>{{ step.key === 'video' ? '视频' : step.key === 'transcript' ? '转写' : step.key === 'comments' ? '评论' : '报告' }}</small></div></div></td><td><div class="row-actions"><button class="more-button" :aria-label="activeMenuTaskId === task.id ? '收起操作菜单' : '打开操作菜单'" :aria-expanded="activeMenuTaskId === task.id" @click.stop="activeMenuTaskId = activeMenuTaskId === task.id ? '' : task.id"><span>⋮</span> 操作 <b>{{ activeMenuTaskId === task.id ? '⌃' : '⌄' }}</b></button><div v-if="activeMenuTaskId === task.id" class="action-menu"><button v-if="task.state === '运行中' || task.state === '待处理'" @click.stop="cancelTask(task)"><span>×</span>取消任务<small>当前步骤返回后停止</small></button><button v-else @click.stop="retryTask(task)"><span>↻</span>重试任务<small>使用原输入重新处理</small></button><button :disabled="task.transcriptState === '转写中'" @click.stop="transcribe(task)"><span>◖</span>{{ task.transcriptState === '已转写' ? '重新转写' : task.transcriptState === '转写中' ? '转写中…' : '转写' }}<small>{{ task.transcriptState === '已转写' ? '覆盖现有转写内容' : task.transcriptState }}</small></button><button :disabled="task.commentsState === '采集中'" @click.stop="pullComments(task)"><span>☷</span>{{ task.commentsState === '已采集' ? '重新拉取评论' : task.commentsState === '采集中' ? '正在拉取评论…' : '拉取评论' }}<small>{{ task.commentsState === '已采集' ? '更新评论及回复' : task.commentsState }}</small></button><div class="menu-divider"></div><button class="danger-action" @click.stop="deleteTarget = task; activeMenuTaskId = ''"><span>⌫</span>删除<small>删除任务记录</small></button></div></div></td></tr><tr v-if="visibleTasks.length === 0"><td colspan="8" class="empty-state">没有符合条件的任务</td></tr></tbody></table></div><div class="table-footer"><span>显示最近 <b>{{ visibleTasks.length }}</b> 条任务</span><div class="pagination"><button disabled>‹</button><button class="current-page">1</button><button disabled>›</button></div></div>
        </section>
        <div class="demo-note"><span>i</span><div><strong>本机任务队列</strong><p>任务状态保存在 SQLite；服务重启后仍可查看和重试。</p></div><button @click="page = 'settings'">前往配置 <span>→</span></button></div>
      </section>

      <section v-else-if="page === 'videos'" class="page-content videos-page">
        <div class="page-heading"><div><div class="eyebrow">CONTENT <span>·</span> 采集结果</div><h1>采集视频</h1><p>查看视频基础信息、互动数据、处理状态和评论内容。</p></div><button class="primary-button" @click="page = 'tasks'; showCreate = true"><span>＋</span> 新建采集</button></div>
        <section class="search-panel video-search-panel"><div class="video-search-main"><label class="search-box video-search-box"><span>⌕</span><input v-model="videoSearch.author" placeholder="作者昵称或作者 ID" @keyup.enter="refreshVideos" /></label><label class="search-box video-search-box"><span>⌕</span><input v-model="videoSearch.title" placeholder="视频标题或简介关键词" @keyup.enter="refreshVideos" /></label><div class="filter-field inline-date video-published-date"><label>视频发布时间</label><div class="range-inputs"><input id="video-published-from" v-model="videoSearch.publishedFrom" type="date" aria-label="视频发布时间开始" /><span>至</span><input v-model="videoSearch.publishedTo" type="date" aria-label="视频发布时间结束" /></div><div class="date-shortcuts"><button @click="setVideoDateRange('today')">今天</button><button @click="setVideoDateRange('yesterday')">昨天</button><button @click="setVideoDateRange('week')">近一周</button><button @click="setVideoDateRange('month')">近一月</button></div></div><button class="primary-button query-button" @click="refreshVideos">查询</button></div><div class="search-foot"><span>可按作者、视频标题和发布时间搜索</span><div><span>已找到 <b>{{ videoTotal }}</b> 条</span><button class="clear-filters" @click="resetVideoSearch">重置条件</button></div></div></section>
        <section class="task-panel video-list-panel"><div class="task-panel-head"><div><h2>视频数据</h2><p>展示最新采集结果</p></div><span class="recent-count">{{ videoTotal }} 条记录</span></div><div class="task-table-wrap"><table class="task-table video-table"><thead><tr><th>视频</th><th>作者</th><th>发布时间</th><th>点赞</th><th>收藏</th><th>转发</th><th>处理状态</th><th>更新时间</th><th>操作</th></tr></thead><tbody><tr v-for="video in videos" :key="`${video.platform}-${video.video_id}`"><td><div class="collected-video"><div class="video-cover"><img v-if="video.cover_url" :src="video.cover_url" alt="" /><span v-else>{{ video.content_type === 'image' ? '▧' : '▶' }}</span></div><div><strong>{{ video.title || '标题未读取' }}</strong><small>{{ video.description || video.video_id }}</small></div></div></td><td><div class="video-author"><strong>{{ video.author_name || '未知作者' }}</strong><small>{{ video.author_id || '—' }}</small></div></td><td class="time-cell">{{ formatDateTime(video.published_at) }}</td><td class="metric-cell">{{ formatCount(video.likes) }}</td><td class="metric-cell">{{ formatCount(video.favorites) }}</td><td class="metric-cell">{{ formatCount(video.shares) }}</td><td><span class="processing-pill" :class="video.metadata_status">{{ processingLabel(video) }}</span></td><td class="time-cell">{{ formatDateTime(video.updated_at) }}</td><td><button class="detail-button" @click="openVideoDetail(video)">查看详情</button></td></tr><tr v-if="videos.length === 0"><td colspan="9" class="empty-state">暂无符合条件的视频数据</td></tr></tbody></table></div></section>
        <section v-if="selectedVideo" class="modal-backdrop" @click.self="selectedVideo = null"><div class="create-panel video-detail-panel" role="dialog" aria-modal="true" aria-labelledby="video-detail-title"><div class="panel-heading"><div><span class="eyebrow">VIDEO DETAIL</span><h2 id="video-detail-title">{{ selectedVideo.title || '视频详情' }}</h2><p>{{ selectedVideo.author_name || '未知作者' }} · {{ formatDateTime(selectedVideo.published_at) }}</p></div><button class="close-button" aria-label="关闭" @click="selectedVideo = null">×</button></div><div class="video-detail-body"><div class="video-detail-summary"><div class="detail-cover"><img v-if="selectedVideo.cover_url" :src="selectedVideo.cover_url" alt="" /><span v-else>{{ selectedVideo.content_type === 'image' ? '▧' : '▶' }}</span></div><dl><div><dt>作者</dt><dd>{{ selectedVideo.author_name || '—' }}</dd></div><div><dt>点赞</dt><dd>{{ formatCount(selectedVideo.likes) }}</dd></div><div><dt>收藏</dt><dd>{{ formatCount(selectedVideo.favorites) }}</dd></div><div><dt>转发</dt><dd>{{ formatCount(selectedVideo.shares) }}</dd></div><div><dt>视频时长</dt><dd>{{ selectedVideo.duration_seconds ? `${selectedVideo.duration_seconds} 秒` : '—' }}</dd></div><div><dt>处理状态</dt><dd>{{ processingLabel(selectedVideo) }}</dd></div></dl></div><section class="detail-section"><h3>视频简介</h3><p>{{ selectedVideo.description || '暂无简介' }}</p><div class="tag-list"><span v-for="tag in selectedVideo.hashtags" :key="tag">#{{ tag }}</span></div></section><section class="detail-section detail-paths"><h3>文件与链接</h3><p><b>作品链接：</b><a :href="selectedVideo.url" target="_blank" rel="noreferrer">{{ selectedVideo.url }}</a></p><p><b>本地文件：</b>{{ selectedVideo.local_media_path || '未下载' }}</p></section><section v-if="selectedVideo.transcript || selectedVideo.transcript_error" class="detail-section"><h3>转写内容</h3><p>{{ selectedVideo.transcript || selectedVideo.transcript_error }}</p></section><section class="detail-section"><h3>评论内容</h3><p v-if="videoCommentsLoading" class="detail-muted">正在读取评论…</p><p v-else-if="selectedVideoComments.length === 0" class="detail-muted">暂无已采集评论</p><div v-else class="comment-list"><article v-for="comment in selectedVideoComments" :key="comment.comment_id" :class="{ reply: comment.parent_id }"><div><strong>{{ comment.author || '匿名用户' }}</strong><span>赞 {{ formatCount(comment.likes) }}</span></div><p>{{ comment.text }}</p></article></div></section></div></div></section>
      </section>

      <section v-else class="page-content settings-page">
        <div class="page-heading"><div><div class="eyebrow">PREFERENCES <span>·</span> 配置管理</div><h1>采集配置</h1><p>按功能管理所有运行参数，新建任务时可作为默认配置使用。</p></div><div class="heading-actions"><button class="secondary-button" @click="resetSettings">恢复默认</button><button class="primary-button" @click="saveSettings">保存配置 <span>✓</span></button></div></div>
        <div class="settings-layout"><aside class="settings-nav"><div class="settings-nav-title">配置分区 <span>{{ groups.length }}</span></div><button v-for="group in groups" :key="group.id" class="settings-nav-item" :class="{ selected: settingsGroup === group.id }" @click="settingsGroup = group.id"><span class="group-icon">{{ group.icon }}</span><span class="group-copy"><strong>{{ group.label }}</strong><small>{{ group.caption }}</small></span><span v-if="settingsGroup === group.id" class="nav-current"></span></button><div class="coverage-note"><span>✓</span><div><strong>配置项已覆盖</strong><small>{{ settingCount }} 项 · 与示例配置对应</small></div></div></aside>
          <div class="settings-content"><div class="settings-content-head"><div><div class="eyebrow">CONFIGURATION</div><h2>{{ activeGroup.label }}</h2><p>{{ activeGroup.caption }}<span class="config-count">{{ visibleSettings.length }} 项配置</span></p></div><label class="search-box settings-search"><span>⌕</span><input v-model="settingsSearch" placeholder="搜索当前分区" /></label></div><div class="settings-card"><div v-for="item in visibleSettings" :key="item.key" class="setting-row" :class="{ 'textarea-row': item.kind === 'textarea' }"><div class="setting-info"><label :for="item.key">{{ item.label }}</label><small>{{ item.description }}</small></div><div class="setting-control" :class="{ 'toggle-control': item.kind === 'toggle' }"><textarea v-if="item.kind === 'textarea'" :id="item.key" :value="stringValue(item.key)" rows="3" placeholder="每行一个链接，可留空" @input="setStringValue(item.key, $event)"></textarea><div v-else-if="item.kind === 'secret'" class="secret-input"><input :id="item.key" :value="stringValue(item.key)" :type="revealKey ? 'text' : 'password'" :placeholder="secretConfigured ? '已配置；留空表示保持不变' : '填写 MiMo API Key'" @input="setStringValue(item.key, $event)" /><button @click="revealKey = !revealKey">{{ revealKey ? '隐藏' : '显示' }}</button></div><select v-else-if="item.kind === 'select'" :id="item.key" :value="stringValue(item.key)" @change="setStringValue(item.key, $event)"><option v-for="option in item.options" :key="option" :value="option">{{ option }}</option></select><button v-else-if="item.kind === 'toggle'" :id="item.key" class="switch" :class="{ on: config[item.key] }" role="switch" :aria-checked="Boolean(config[item.key])" @click="config[item.key] = !config[item.key]"><span></span></button><div v-else class="value-input"><input :id="item.key" :value="stringValue(item.key)" :type="item.kind === 'number' ? 'number' : 'text'" :placeholder="item.kind === 'number' ? '不限制' : ''" @input="setStringValue(item.key, $event)" /><span v-if="item.unit">{{ item.unit }}</span></div><code>{{ item.key }}</code></div></div><div v-if="visibleSettings.length === 0" class="empty-settings">没有找到匹配的配置项</div></div><div class="settings-tip"><span>i</span><p>配置从本机后端读取，保存前会校验；已设置的 API Key 留空时会继续保留。</p></div></div>
        </div>
      </section>
    </main>
    <div v-if="deleteTarget" class="modal-backdrop confirm-backdrop" @click.self="deleteTarget = null"><section class="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="delete-title"><div class="confirm-icon">!</div><h2 id="delete-title">删除这条任务记录？</h2><p>“{{ deleteTarget.title }}”将从任务列表移除，已经采集的视频、评论和报告仍会保留。</p><div class="confirm-actions"><button class="secondary-button" @click="deleteTarget = null">取消</button><button class="danger-button" @click="confirmDelete">确认删除</button></div></section></div>
    <Transition name="toast"><div v-if="toast" class="toast-message"><span>✓</span>{{ toast }}</div></Transition>
  </div>
</template>
