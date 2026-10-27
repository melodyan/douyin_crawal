<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

type Page = 'overview' | 'tasks' | 'videos' | 'settings'
type TaskMode = 'video' | 'collection' | 'author'
type TaskModule = 'download' | 'transcribe' | 'comments' | 'report'
type TaskState = '已暂停' | '运行中' | '已完成' | '待处理' | '失败' | '已取消'
type TranscriptState = '未转写' | '转写中' | '已转写'
type CommentsState = '未采集' | '采集中' | '已采集'
interface Task {
  requestMode: string
  options: Record<string, any>
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
}

interface CollectedVideo {
  download_complete: boolean
  comments_complete: boolean
  report_complete: boolean
  running_actions: string[]
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

interface VideoComment { marked: boolean; comment_id: string; text: string; likes: number; author: string; parent_id: string; reply_to_id: string; author_id: string; published_at: string }

function readSearch(key: string): Record<string, any> {
  try { const value = JSON.parse(localStorage.getItem(key) || '{}'); return value && typeof value === 'object' && !Array.isArray(value) ? value : {} } catch { return {} }
}
function searchText(value: unknown, fallback = '') { return typeof value === 'string' ? value : fallback }
function searchList(value: unknown): string[] { return Array.isArray(value) ? value.filter((item) => typeof item === 'string') : [] }
const savedTasks = readSearch('douyin-task-search-v1')
const savedVideos = readSearch('douyin-video-search-v1')
const storedPage = localStorage.getItem('douyin-current-page')
const page = ref<Page>(storedPage === 'overview' || storedPage === 'tasks' || storedPage === 'videos' || storedPage === 'settings' ? storedPage : 'tasks')
const toast = ref('')
const showCreate = ref(false)
const activeMode = ref<TaskMode>('video')
const activeModules = ref<TaskModule[]>(['download', 'transcribe', 'comments', 'report'])
const taskName = ref('')
const sourceUrl = ref('')
const tempConfig = ref(false)
const selectedSettings = ref<string[]>([])
const taskConfigOptions = [
  { key: 'crawl.max_comments_per_video', option: 'max_comments_per_video', label: '单视频评论上限', kind: 'number' },
  { key: 'inputs.max_videos_per_profile', option: 'max_videos_per_profile', label: '作者作品上限', kind: 'number' },
  { key: 'inputs.max_videos_per_collection', option: 'max_videos_per_collection', label: '合集作品上限', kind: 'number' },
  { key: 'crawl.refresh_completed', option: 'force_reprocess', label: '重新处理已完成作品', kind: 'toggle' },
]
const taskConfig = reactive<Record<string, string | number | boolean>>({})
const settingsGroup = ref('inputs')
const revealKey = ref(false)
const secretConfigured = ref(false)
const taskFilter = ref(searchText(savedTasks.status, '全部任务'))
const taskStatusOpen = ref(false)
const taskStatusPicker = ref<HTMLElement | null>(null)
const taskStatusOptions = [
  { label: '全部任务', tone: 'all' }, { label: '运行中', tone: 'running' },
  { label: '已完成', tone: 'done' }, { label: '待处理', tone: 'pending' },
  { label: '已暂停', tone: 'pending' }, { label: '失败', tone: 'failed' }, { label: '已取消', tone: 'cancelled' },
]
function closeTaskStatus(event: MouseEvent) {
  if (taskStatusPicker.value && !taskStatusPicker.value.contains(event.target as Node)) taskStatusOpen.value = false
}
function chooseTaskStatus(label: string) { taskFilter.value = label; taskStatusOpen.value = false; applyFilters() }
function moveTaskStatus(event: KeyboardEvent) {
  const step = event.key === 'ArrowDown' ? 1 : event.key === 'ArrowUp' ? -1 : 0
  if (!step) return
  event.preventDefault()
  const buttons = taskStatusPicker.value?.querySelectorAll<HTMLButtonElement>('[role=option]')
  if (!buttons?.length) { taskStatusOpen.value = true; return }
  const index = [...buttons].indexOf(document.activeElement as HTMLButtonElement)
  buttons[(index + step + buttons.length) % buttons.length]?.focus()
}
const appliedTaskFilter = ref(taskFilter.value)
const deleteTarget = ref<Task | null>(null)
const errorTarget = ref<Task | null>(null)
const videos = ref<CollectedVideo[]>([])
const videoTotal = ref(0)
const authorVideoGroups = computed(() => {
  const groups = new Map<string, { key: string; name: string; authorId: string; videos: CollectedVideo[] }>()
  for (const video of videos.value) {
    const key = JSON.stringify([video.platform, video.author_id || video.author_name || `unknown:${video.video_id}`])
    let group = groups.get(key)
    if (!group) {
      group = { key, name: video.author_name || '未知作者', authorId: video.author_id, videos: [] }
      groups.set(key, group)
    }
    group.videos.push(video)
  }
  return [...groups.values()]
})
const videoSearch = reactive({ title: searchText(savedVideos.title), publishedFrom: searchText(savedVideos.publishedFrom, localDate(-6)), publishedTo: searchText(savedVideos.publishedTo, localDate()) })
const selectedAuthorNames = ref<string[]>(searchList(savedVideos.authors))
const authorOptions = ref<{ name: string; count: number }[]>([])
const authorSearch = ref('')
const authorDropdownOpen = ref(false)
const authorOptionsLoading = ref(false)
const authorOptionsFailed = ref(false)
const authorPicker = ref<HTMLElement | null>(null)
let authorSearchTimer: ReturnType<typeof setTimeout> | undefined
let authorRequestVersion = 0
async function loadAuthorOptions() {
  const version = ++authorRequestVersion
  authorOptionsLoading.value = true
  authorOptionsFailed.value = false
  try {
    const result = await api<{ items: { name: string; count: number }[] }>(`/api/video-authors?q=${encodeURIComponent(authorSearch.value)}`)
    if (version === authorRequestVersion) authorOptions.value = result.items
  } catch { if (version === authorRequestVersion) authorOptionsFailed.value = true }
  finally { if (version === authorRequestVersion) authorOptionsLoading.value = false }
}
function toggleAuthorDropdown() {
  authorDropdownOpen.value = !authorDropdownOpen.value
  if (authorDropdownOpen.value) loadAuthorOptions()
}
function closeAuthorDropdown(event: MouseEvent) {
  if (authorPicker.value && !authorPicker.value.contains(event.target as Node)) authorDropdownOpen.value = false
}
watch(authorSearch, () => { clearTimeout(authorSearchTimer); authorSearchTimer = setTimeout(loadAuthorOptions, 200) })
const selectedTags = ref<string[]>(searchList(savedVideos.tags))
const tagOptions = ref<{ name: string; count: number }[]>([])
const tagSearch = ref('')
const tagDropdownOpen = ref(false)
const tagOptionsLoading = ref(false)
const tagOptionsFailed = ref(false)
const tagPicker = ref<HTMLElement | null>(null)
let tagSearchTimer: ReturnType<typeof setTimeout> | undefined
let tagRequestVersion = 0
async function loadTagOptions() {
  const version = ++tagRequestVersion
  tagOptionsLoading.value = true
  tagOptionsFailed.value = false
  try {
    const result = await api<{ items: { name: string; count: number }[] }>(`/api/video-tags?q=${encodeURIComponent(tagSearch.value)}`)
    if (version === tagRequestVersion) tagOptions.value = result.items
  } catch { if (version === tagRequestVersion) tagOptionsFailed.value = true }
  finally { if (version === tagRequestVersion) tagOptionsLoading.value = false }
}
function toggleTagDropdown() {
  tagDropdownOpen.value = !tagDropdownOpen.value
  if (tagDropdownOpen.value) loadTagOptions()
}
function closeTagDropdown(event: MouseEvent) {
  if (tagPicker.value && !tagPicker.value.contains(event.target as Node)) tagDropdownOpen.value = false
}
watch(tagSearch, () => { clearTimeout(tagSearchTimer); tagSearchTimer = setTimeout(loadTagOptions, 200) })
const taskDateDropdownOpen = ref(false)
const taskDatePicker = ref<HTMLElement | null>(null)
const taskDateLabel = computed(() => {
  const from = filters.collectedFrom
  const to = filters.collectedTo
  return from && to ? `${from} 至 ${to}` : from ? `${from} 起` : to ? `截至 ${to}` : '任务创建时间'
})
function closeTaskDateDropdown(event: MouseEvent) {
  if (taskDatePicker.value && !taskDatePicker.value.contains(event.target as Node)) taskDateDropdownOpen.value = false
}
const dateDropdownOpen = ref(false)
const datePicker = ref<HTMLElement | null>(null)
const videoDateLabel = computed(() => {
  const from = videoSearch.publishedFrom
  const to = videoSearch.publishedTo
  return from && to ? `${from} 至 ${to}` : from ? `${from} 起` : to ? `截至 ${to}` : '全部发布时间'
})
function closeDateDropdown(event: MouseEvent) {
  if (datePicker.value && !datePicker.value.contains(event.target as Node)) dateDropdownOpen.value = false
}
const videoSourceUrl = ref(searchText(savedVideos.sourceUrl))
let videoRequestVersion = 0
const videosLoading = ref(false)
const videoLoadError = ref('')
const selectedVideo = ref<CollectedVideo | null>(null)
const deleteVideoTarget = ref<CollectedVideo | null>(null)
const selectedVideoComments = ref<VideoComment[]>([])
const markedCommentsOnly = ref(false)
const markingComments = ref<string[]>([])
const markedCommentCount = computed(() => selectedVideoComments.value.filter((comment) => comment.marked).length)
const visibleVideoComments = computed(() => markedCommentsOnly.value ? selectedVideoComments.value.filter((comment) => comment.marked) : selectedVideoComments.value)
async function toggleCommentMark(comment: VideoComment) {
  const video = selectedVideo.value
  if (!video || markingComments.value.includes(comment.comment_id)) return
  markingComments.value.push(comment.comment_id)
  try {
    const result = await api<{ marked: boolean }>(`/api/videos/${encodeURIComponent(video.platform)}/${encodeURIComponent(video.video_id)}/comments/${encodeURIComponent(comment.comment_id)}/mark`, { method: 'PUT', body: JSON.stringify({ marked: !comment.marked }) })
    comment.marked = result.marked
  } catch (error) { notify(`标记保存失败：${(error as Error).message}`) }
  finally { markingComments.value = markingComments.value.filter((id) => id !== comment.comment_id) }
}
const videoCommentsLoading = ref(false)
const videoPlaybackError = ref(false)
function localDate(offsetDays = 0) { return dateInShanghai(new Date(Date.now() + offsetDays * 86400000).toISOString()) }
const filters = reactive({ videoName: searchText(savedTasks.videoName), collectedFrom: searchText(savedTasks.collectedFrom, localDate(-6)), collectedTo: searchText(savedTasks.collectedTo, localDate()) })
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
  const state: TaskState = item.state === 'paused' ? '已暂停' : item.state === 'done' ? '已完成' : item.state === 'failed' ? '失败' : item.state === 'cancelled' ? '已取消' : item.state === 'running' ? '运行中' : '待处理'
  const steps = item.steps || {}
  const input = (item.urls || []).join('\n')
  const displayMode = input.includes('/collection/') ? '短视频合集' : input.includes('/user/') ? '指定作者' : '单个短视频'
  return { requestMode: item.mode, options: item.options || {}, id: item.id, title: item.title || item.mode || '采集任务', mode: displayMode, module: item.mode === 'run' ? '完整处理' : '仅采集', input, state, progress: item.progress || 0, createdAt: item.created_at || '', collectedAt: dateInShanghai(item.created_at || ''), publishedAt: '', author: '采集任务', transcript: item.error || '', likes: 0, favorites: 0, shares: 0, transcriptState: steps.transcribe === 'done' ? '已转写' : steps.transcribe === 'running' ? '转写中' : '未转写', commentsState: steps.comments === 'done' ? '已采集' : steps.comments === 'running' ? '采集中' : '未采集', inReadingList: readingIds.has(item.id)}
}
async function refreshTasks() {
  try { const result = await api<{ items: any[] }>('/api/tasks'); tasks.value = result.items.map(taskFromApi) } catch (error) { notify(`后端未连接：${(error as Error).message}`) }
}
async function refreshVideos() {
  const version = ++videoRequestVersion
  if (videoSearch.publishedFrom && videoSearch.publishedTo && videoSearch.publishedFrom > videoSearch.publishedTo) {
    videosLoading.value = false
    notify('开始日期不能晚于结束日期')
    return
  }
  videosLoading.value = true
  videoLoadError.value = ''
  const query = new URLSearchParams()
  for (const name of selectedAuthorNames.value) query.append('author_names', name)
  for (const tag of selectedTags.value) query.append('tags', tag)
  if (videoSearch.title.trim()) query.set('title', videoSearch.title.trim())
  if (videoSearch.publishedFrom) query.set('published_from', videoSearch.publishedFrom)
  if (videoSearch.publishedTo) query.set('published_to', videoSearch.publishedTo)
  if (videoSourceUrl.value) query.set('source_url', videoSourceUrl.value)
  try {
    query.set('limit', '500')
    const result = await api<{ items: CollectedVideo[]; total: number }>(`/api/videos?${query.toString()}`)
    while (result.items.length < result.total && version === videoRequestVersion) {
      query.set('offset', String(result.items.length))
      const next = await api<{ items: CollectedVideo[]; total: number }>(`/api/videos?${query.toString()}`)
      if (!next.items.length) break
      result.items.push(...next.items)
    }
    if (version !== videoRequestVersion) return
    videos.value = result.items
    if (selectedVideo.value) {
      const updated = result.items.find((item) => item.platform === selectedVideo.value?.platform && item.video_id === selectedVideo.value?.video_id)
      if (updated) Object.assign(selectedVideo.value, updated)
    }
    videoTotal.value = result.total
    return result
  } catch (error) { if (version === videoRequestVersion) { videoLoadError.value = (error as Error).message; notify(`视频数据读取失败：${videoLoadError.value}`) } }
  finally { if (version === videoRequestVersion) videosLoading.value = false }
}
async function openVideoDetail(video: CollectedVideo) {
  selectedVideo.value = video
  videoPlaybackError.value = false
  selectedVideoComments.value = []
  markedCommentsOnly.value = false
  videoCommentsLoading.value = true
  try {
    const result = await api<{ items: VideoComment[]; total: number }>(`/api/videos/${encodeURIComponent(video.platform)}/${encodeURIComponent(video.video_id)}/comments?limit=500`)
    while (result.items.length < result.total) {
      const next = await api<{ items: VideoComment[]; total: number }>(`/api/videos/${encodeURIComponent(video.platform)}/${encodeURIComponent(video.video_id)}/comments?limit=500&offset=${result.items.length}`)
      if (!next.items.length) break
      result.items.push(...next.items)
    }
    if (selectedVideo.value?.video_id === video.video_id && selectedVideo.value?.platform === video.platform) selectedVideoComments.value = result.items
  } catch (error) { notify(`评论读取失败：${(error as Error).message}`) }
  finally { videoCommentsLoading.value = false }
}
async function confirmDeleteVideo() {
  if (!deleteVideoTarget.value) return
  const target = deleteVideoTarget.value
  try {
    await api(`/api/videos/${encodeURIComponent(target.platform)}/${encodeURIComponent(target.video_id)}`, { method: 'DELETE' })
    videos.value = videos.value.filter((video) => video.platform !== target.platform || video.video_id !== target.video_id)
    videoTotal.value = Math.max(0, videoTotal.value - 1)
    if (selectedVideo.value?.platform === target.platform && selectedVideo.value.video_id === target.video_id) selectedVideo.value = null
    deleteVideoTarget.value = null
    notify(`已删除视频记录“${target.title || target.video_id}”`)
  } catch (error) { notify(`删除视频失败：${(error as Error).message}`) }
}
function resetVideoSearch() { videoSourceUrl.value = ''; selectedAuthorNames.value = []; authorSearch.value = ''; authorDropdownOpen.value = false; selectedTags.value = []; tagSearch.value = ''; tagDropdownOpen.value = false; dateDropdownOpen.value = false; videoSearch.title = ''; videoSearch.publishedFrom = localDate(-6); videoSearch.publishedTo = localDate(); refreshVideos() }
async function openTaskVideos(task: Task) {
  const source = task.input.split('\n').map((item) => item.trim()).find(Boolean)
  if (!source) { notify('该任务没有可用的采集链接'); return }
  page.value = 'videos'
  videoSourceUrl.value = source
  selectedAuthorNames.value = []
  selectedTags.value = []
  tagSearch.value = ''
  tagDropdownOpen.value = false
  authorSearch.value = ''
  authorDropdownOpen.value = false
  videoSearch.title = ''
  videoSearch.publishedFrom = ''
  videoSearch.publishedTo = ''
  const result = await refreshVideos()
  if (result && videoSourceUrl.value === source) {
    const names = [...new Set(result.items.map((video) => video.author_name || '未知作者'))]
    if (names.length === 1) selectedAuthorNames.value = names
  }
}
function clearVideoSource() { videoSourceUrl.value = ''; refreshVideos() }
function clearVideoDates() { videoSearch.publishedFrom = ''; videoSearch.publishedTo = ''; dateDropdownOpen.value = false; refreshVideos() }
function setVideoDateRange(range: 'today' | 'yesterday' | 'week' | 'month') {
  const endOffset = range === 'yesterday' ? -1 : 0
  const startOffset = range === 'week' ? -6 : range === 'month' ? -29 : endOffset
  videoSearch.publishedFrom = localDate(startOffset)
  videoSearch.publishedTo = localDate(endOffset)
  dateDropdownOpen.value = false
  refreshVideos()
}
function formatCount(value: number) { return new Intl.NumberFormat('zh-CN').format(value || 0) }
function dateInShanghai(value: string) {
  if (!value || Number.isNaN(new Date(value).getTime())) return ''
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(value))
  const part = (type: string) => parts.find((item) => item.type === type)?.value
  return `${part('year')}-${part('month')}-${part('day')}`
}
function formatDateTime(value: string) { return !value || Number.isNaN(new Date(value).getTime()) ? '—' : new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) }
function processingLabel(video: CollectedVideo) { return video.transcript_status === 'complete' ? '已转写' : video.local_media_path ? '已下载' : video.metadata_status === 'failed' ? '采集失败' : '已采集' }
type VideoAction = 'download' | 'comments' | 'transcribe' | 'report' | 'refresh'
const videoActions: { key: VideoAction; done: string; pending: string; label: string; icon: string }[] = [
  { key: 'download', label: '下载', done: '视频已下载', pending: '视频未下载', icon: '↓' },
  { key: 'comments', label: '评论', done: '评论已抓取', pending: '评论未抓取', icon: '☰' },
  { key: 'transcribe', label: '转写', done: '转写已完成', pending: '转写未完成', icon: '文' },
  { key: 'report', label: '报告', done: '已生成报告', pending: '未生成报告', icon: '▤' },
]
const submittingVideoActions = ref<string[]>([])
function videoActionDone(video: CollectedVideo, action: VideoAction) {
  if (action === 'refresh') return false
  return action === 'download' ? video.download_complete : action === 'comments' ? video.comments_complete : action === 'transcribe' ? video.transcript_status === 'complete' : video.report_complete
}
function videoActionBusy(video: CollectedVideo, action: VideoAction) {
  return submittingVideoActions.value.includes(`${video.video_id}:${action}`) || video.running_actions?.includes(action)
}
async function startVideoAction(video: CollectedVideo, action: VideoAction) {
  const key = `${video.video_id}:${action}`
  if (action === 'report' && video.report_complete) {
    try {
      const result = await api<{ path: string }>(`/api/videos/${encodeURIComponent(video.platform)}/${encodeURIComponent(video.video_id)}/report/reveal`, { method: 'POST' })
      notify(`报告位置：${result.path}`)
    } catch (error) { notify(`打开报告失败：${(error as Error).message}`) }
    return
  }
  if (videoActionDone(video, action) || videoActionBusy(video, action)) return
  submittingVideoActions.value.push(key)
  try {
    await api(`/api/videos/${encodeURIComponent(video.platform)}/${encodeURIComponent(video.video_id)}/actions/${action}`, { method: 'POST' })
    notify('已启动对应流程，可在任务列表查看进度和错误')
    await refreshVideos()
    await refreshTasks()
  } catch (error) { notify(`启动失败：${(error as Error).message}`) }
  finally { submittingVideoActions.value = submittingVideoActions.value.filter((item) => item !== key) }
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

type BatchKind = 'tasks' | 'videos'
const selectedTaskIds = ref<string[]>([])
const selectedVideoIds = ref<string[]>([])
const batchBusy = ref(false)
const batchConfirm = ref<BatchKind | null>(null)
const batchResult = ref<{ summary: string; details: string[] } | null>(null)
const batchVideoAction = ref<VideoAction | 'delete'>('refresh')
const batchTaskAction = ref<'update' | 'retry' | 'delete'>('update')
const videoKey = (video: CollectedVideo) => `${video.platform}:${video.video_id}`
function toggleAllTasks(checked: boolean) { selectedTaskIds.value = checked ? visibleTasks.value.map((task) => task.id) : [] }
function toggleAllVideos(items: CollectedVideo[], checked: boolean) {
  const keys = new Set(items.map(videoKey))
  selectedVideoIds.value = checked ? [...new Set([...selectedVideoIds.value, ...keys])] : selectedVideoIds.value.filter((key) => !keys.has(key))
}
watch(visibleTasks, (items) => { const ids = new Set(items.map((task) => task.id)); selectedTaskIds.value = selectedTaskIds.value.filter((id) => ids.has(id)) })
watch(videos, (items) => { const ids = new Set(items.map(videoKey)); selectedVideoIds.value = selectedVideoIds.value.filter((id) => ids.has(id)) })
function requestBatch(kind: BatchKind) {
  if (batchBusy.value) return
  const action = kind === 'tasks' ? batchTaskAction.value : batchVideoAction.value
  if (action === 'delete') { batchConfirm.value = kind; return }
  executeBatch(kind, action)
}
async function executeBatch(kind: BatchKind, action: string) {
  if (batchBusy.value) return
  const items = kind === 'tasks'
    ? visibleTasks.value.filter((task) => selectedTaskIds.value.includes(task.id))
    : videos.value.filter((video) => selectedVideoIds.value.includes(videoKey(video)))
  if (!items.length) { notify('请先勾选要操作的记录'); return }
  batchBusy.value = true
  batchConfirm.value = null
  let success = 0, skipped = 0, failed = 0
  const details: string[] = []
  try {
    for (const item of items) {
      const title = item.title || '未命名记录'
      let reason = '', path = '', method = 'POST'
      if (kind === 'tasks') {
        const task = item as Task
        if (task.state === '运行中' || task.state === '待处理') reason = '任务正在处理中'
        else if (action === 'update' && !['短视频合集', '指定作者'].includes(task.mode)) reason = '仅合集和作者任务支持更新'
        path = `/api/tasks/${task.id}${action === 'delete' ? '' : '/' + action}`
      } else {
        const video = item as CollectedVideo
        if (video.running_actions?.length || submittingVideoActions.value.some((key) => key.startsWith(video.video_id + ':'))) reason = '视频流程正在处理中'
        else if (action !== 'delete' && action !== 'refresh' && videoActionDone(video, action as VideoAction)) reason = '该流程已完成'
        else if (action === 'transcribe' && video.content_type === 'image') reason = '图文不支持转写'
        else if (action === 'transcribe' && !video.download_complete) reason = '请先下载视频'
        path = `/api/videos/${encodeURIComponent(video.platform)}/${encodeURIComponent(video.video_id)}${action === 'delete' ? '' : '/actions/' + action}`
      }
      if (reason) { skipped++; details.push(`跳过：${title} — ${reason}`); continue }
      if (action === 'delete') method = 'DELETE'
      try {
        await api(path, { method })
        success++
        if (kind === 'tasks') selectedTaskIds.value = selectedTaskIds.value.filter((id) => id !== (item as Task).id)
        else selectedVideoIds.value = selectedVideoIds.value.filter((id) => id !== videoKey(item as CollectedVideo))
      } catch (error) { failed++; details.push(`失败：${title} — ${(error as Error).message}`) }
    }
    batchResult.value = { summary: `${action === 'delete' ? '已删除' : '已提交'} ${success} 条，跳过 ${skipped} 条，失败 ${failed} 条`, details }
    await refreshTasks()
    if (kind === 'videos') await refreshVideos()
  } finally { batchBusy.value = false }
}
function notify(message: string) {
  toast.value = message
  window.setTimeout(() => { if (toast.value === message) toast.value = '' }, 2400)
}
async function createTask() {
  if (editSaving.value) return
  if (!taskName.value.trim()) { notify('请填写采集任务名称'); return }
  if (!sourceUrl.value.trim()) { notify(transcribeOnly.value ? '请填写本地视频绝对路径' : '请先填写抖音链接'); return }
  if (!activeModules.value.length) { notify('至少选择一个处理模块'); return }
  if (transcribeOnly.value && !/^(?:[A-Za-z]:[\\/]|\/)/.test(sourceUrl.value.trim())) { notify('本地视频请填写绝对路径'); return }
  if (activeModules.value.includes('transcribe') && !transcribeOnly.value && !activeModules.value.includes('download')) { notify('转写与其他模块同时使用时，请同时选择“下载视频”'); return }
  const options: Record<string, unknown> = {
    ...(editTarget.value?.options || {}),
    title: taskName.value.trim(),
    download_video: activeModules.value.includes('download'),
    transcribe_text: activeModules.value.includes('transcribe'),
    include_comments: activeModules.value.includes('comments'),
    generate_report: activeModules.value.includes('report'),
  }
  delete options.local_video_path
  delete options.refresh_completed
  if (transcribeOnly.value) options.local_video_path = sourceUrl.value.trim()
  for (const setting of taskConfigOptions) {
    delete options[setting.option]
    if (tempConfig.value && selectedSettings.value.includes(setting.key)) {
      const value = taskConfig[setting.key]
      if (setting.kind === 'number' && value !== '' && (!Number.isInteger(Number(value)) || Number(value) < 1)) { notify(`${setting.label}请填写大于 0 的整数，留空表示不限制`); return }
      options[setting.option] = setting.kind === 'toggle' ? Boolean(value) : value === '' ? null : Number(value)
    }
  }
  const taskMode = activeModules.value.some((item) => item !== 'comments') ? 'run' : 'collect'
  const editing = editTarget.value
  editSaving.value = true
  try {
    await api(editing ? `/api/tasks/${editing.id}` : '/api/tasks', { method: editing ? 'PUT' : 'POST', body: JSON.stringify({ mode: taskMode, urls: transcribeOnly.value ? [] : sourceUrl.value.split('\n').map((url) => url.trim()).filter(Boolean), options }) })
    closeTaskForm()
    notify(editing ? '任务及配置已保存，下次执行时生效' : `已创建“${taskName.value}”任务`)
    await refreshTasks()
  } catch (error) { notify(`${editing ? '修改' : '创建'}失败：${(error as Error).message}`) }
  finally { editSaving.value = false }
}
function toggleModule(id: TaskModule) {
  if (activeModules.value.includes(id)) {
    activeModules.value = activeModules.value.filter((module) => module !== id)
  } else {
    activeModules.value = [...activeModules.value, id]
  }
}
function toggleReading(task: Task) {
  task.inReadingList = !task.inReadingList
  if (task.inReadingList) readingIds.add(task.id); else readingIds.delete(task.id)
  localStorage.setItem('douyin-reading-tasks', JSON.stringify([...readingIds]))
  notify(task.inReadingList ? '已加入阅读' : '已从阅读列表移除')
}
function confirmDelete() {
  if (!deleteTarget.value) return
  const title = deleteTarget.value.title
  const id = deleteTarget.value.id
  api(`/api/tasks/${id}`, { method: 'DELETE' }).then(() => { tasks.value = tasks.value.filter((task) => task.id !== id); deleteTarget.value = null; notify(`已删除“${title}”`) }).catch((error) => notify(`删除失败：${error.message}`))
}
let eventSource: EventSource | null = null
onMounted(() => { document.addEventListener('click', closeAuthorDropdown); document.addEventListener('click', closeTagDropdown); document.addEventListener('click', closeDateDropdown); document.addEventListener('click', closeTaskDateDropdown); document.addEventListener('click', closeTaskStatus); document.documentElement.style.setProperty('--font-offset', `${fontOffset.value}px`); refreshTasks(); refreshVideos(); loadSettings(); eventSource = new EventSource(`${API_BASE}/api/tasks/events`); eventSource.addEventListener('tasks', (event) => { tasks.value = JSON.parse((event as MessageEvent).data).map(taskFromApi); if (page.value === 'videos') refreshVideos() }) })
onBeforeUnmount(() => { document.removeEventListener('click', closeAuthorDropdown); document.removeEventListener('click', closeTagDropdown); document.removeEventListener('click', closeDateDropdown); document.removeEventListener('click', closeTaskDateDropdown); document.removeEventListener('click', closeTaskStatus); clearTimeout(authorSearchTimer); clearTimeout(tagSearchTimer); eventSource?.close() })
function clearFilters() {
  taskDateDropdownOpen.value = false
  taskFilter.value = '全部任务'
  appliedTaskFilter.value = '全部任务'
  Object.assign(filters, { videoName: '', collectedFrom: localDate(-6), collectedTo: localDate() })
  Object.assign(appliedFilters, filters)
}
function setDateRange(range: 'today' | 'yesterday' | 'week' | 'month') {
  const endOffset = range === 'yesterday' ? -1 : 0
  const startOffset = range === 'week' ? -6 : range === 'month' ? -29 : endOffset
  filters.collectedFrom = localDate(startOffset)
  filters.collectedTo = localDate(endOffset)
  taskDateDropdownOpen.value = false
  applyFilters()
}
function applyFilters() {
  if (filters.collectedFrom && filters.collectedTo && filters.collectedFrom > filters.collectedTo) {
    notify('开始日期不能晚于结束日期')
    return
  }
  appliedTaskFilter.value = taskFilter.value
  Object.assign(appliedFilters, filters)
  notify(`查询完成，找到 ${visibleTasks.value.length} 条任务`)
}
function changeFontSize(delta: number) { fontOffset.value = Math.min(6, Math.max(0, fontOffset.value + delta)); document.documentElement.style.setProperty('--font-offset', `${fontOffset.value}px`); localStorage.setItem('douyin-font-offset', String(fontOffset.value)) }
const keyLoading = ref(false)
async function toggleApiKey() {
  if (revealKey.value) { revealKey.value = false; return }
  if (!config['asr.api_key'] && secretConfigured.value) {
    keyLoading.value = true
    try {
      const result = await api<{ value: string }>('/api/config/api-key')
      config['asr.api_key'] = result.value
    } catch (error) {
      notify(`读取 API Key 失败：${(error as Error).message}`)
      return
    } finally { keyLoading.value = false }
  }
  revealKey.value = true
}
watch([filters, taskFilter], () => localStorage.setItem('douyin-task-search-v1', JSON.stringify({ ...filters, status: taskFilter.value })), { deep: true, flush: 'sync' })
watch([videoSearch, selectedAuthorNames, selectedTags, videoSourceUrl], () => localStorage.setItem('douyin-video-search-v1', JSON.stringify({ ...videoSearch, authors: selectedAuthorNames.value, tags: selectedTags.value, sourceUrl: videoSourceUrl.value })), { deep: true, flush: 'sync' })
const pausingTasks = ref<string[]>([])
async function pauseOrResume(task: Task) {
  if (pausingTasks.value.includes(task.id)) return
  pausingTasks.value.push(task.id)
  try {
    await api(`/api/tasks/${task.id}/${task.state === '已暂停' ? 'resume' : 'pause'}`, { method: 'POST' })
    notify(task.state === '已暂停' ? '任务已继续' : '已请求暂停，当前步骤结束后停止')
    await refreshTasks()
  } catch (error) { notify((error as Error).message) }
  finally { pausingTasks.value = pausingTasks.value.filter((id) => id !== task.id) }
}
const editTarget = ref<Task | null>(null)
const editSaving = ref(false)
function initTaskConfig(task?: Task) {
  for (const setting of taskConfigOptions) taskConfig[setting.key] = task?.options[setting.option] ?? config[setting.key] ?? ''
  // An explicit null means unlimited, even if the global configuration has a limit.
  for (const setting of taskConfigOptions) if (task && task.options[setting.option] === null) taskConfig[setting.key] = ''
  selectedSettings.value = task ? taskConfigOptions.filter((setting) => Object.prototype.hasOwnProperty.call(task.options, setting.option)).map((setting) => setting.key) : []
  tempConfig.value = !!task
}
function openNewTask() {
  editTarget.value = null
  taskName.value = ''
  sourceUrl.value = ''
  activeMode.value = 'video'
  activeModules.value = ['download', 'transcribe', 'comments', 'report']
  initTaskConfig()
  page.value = 'tasks'
  showCreate.value = true
}
function closeTaskForm() { showCreate.value = false; editTarget.value = null }
function editTask(task: Task) {
  editTarget.value = task
  taskName.value = task.title
  sourceUrl.value = task.options.local_video_path || task.input
  activeMode.value = task.mode === '指定作者' ? 'author' : task.mode === '短视频合集' ? 'collection' : 'video'
  activeModules.value = moduleOptions.filter((module) => task.options[({ download: 'download_video', transcribe: 'transcribe_text', comments: 'include_comments', report: 'generate_report' })[module.id]] ?? (task.requestMode === 'run' || module.id === 'comments')).map((module) => module.id)
  initTaskConfig(task)
  page.value = 'tasks'
  showCreate.value = true
}
const updatingTasks = ref<string[]>([])
async function updateTask(task: Task) {
  if (updatingTasks.value.includes(task.id) || retryingTasks.value.includes(task.id) || task.state === '运行中' || task.state === '待处理') return
  updatingTasks.value.push(task.id)
  try {
    await api(`/api/tasks/${task.id}/update`, { method: 'POST' })
    notify('已启动增量更新，只采集尚未入库的视频')
    await refreshTasks()
  } catch (error) { notify(`更新失败：${(error as Error).message}`) }
  finally { updatingTasks.value = updatingTasks.value.filter((id) => id !== task.id) }
}
const retryingTasks = ref<string[]>([])
async function retryTask(task: Task) {
  if (retryingTasks.value.includes(task.id) || updatingTasks.value.includes(task.id) || task.state === '运行中' || task.state === '待处理') return
  retryingTasks.value.push(task.id)
  try {
    await api(`/api/tasks/${task.id}/retry`, { method: 'POST' })
    notify(`已重新启动“${task.title}”`)
    await refreshTasks()
  } catch (error) {
    notify(`重试失败：${(error as Error).message}`)
  } finally {
    retryingTasks.value = retryingTasks.value.filter((id) => id !== task.id)
  }
}
function saveSettings() {
  api('/api/config', { method: 'PUT', body: JSON.stringify({ values: { ...config } }) }).then(() => { secretConfigured.value ||= Boolean(config['asr.api_key']); config['asr.api_key'] = ''; revealKey.value = false; notify('配置已保存到后端') }).catch((error) => notify(`配置保存失败：${error.message}`))
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
        <div class="page-heading"><div><div class="eyebrow">WORKSPACE <span>·</span> 数据总览</div><h1>概况预览</h1><p>集中查看采集任务与内容处理情况。</p></div><button class="primary-button" @click="openNewTask"><span>＋</span> 新建任务</button></div>
        <div class="stats-grid"><article class="stat-card"><div class="stat-top"><span>全部任务</span><span class="stat-icon indigo">▦</span></div><strong>{{ tasks.length }}</strong><small>本地任务记录</small></article><article class="stat-card"><div class="stat-top"><span>运行中</span><span class="stat-icon blue">◌</span></div><strong>{{ tasks.filter(t => t.state === '运行中').length }}</strong><small><i class="status-dot"></i>正在处理</small></article><article class="stat-card"><div class="stat-top"><span>已完成</span><span class="stat-icon green">✓</span></div><strong>{{ tasks.filter(t => t.state === '已完成').length }}</strong><small>处理成功</small></article><article class="stat-card"><div class="stat-top"><span>需要关注</span><span class="stat-icon amber">!</span></div><strong>{{ tasks.filter(t => t.state === '失败').length }}</strong><small>查看失败原因</small></article></div>
        <div class="overview-grid"><section class="overview-card"><div class="overview-card-head"><div><h2>最近任务</h2><p>最近加入本机队列的采集任务</p></div><button class="text-link" @click="page = 'tasks'">查看全部 →</button></div><div v-for="task in tasks.slice(0, 4)" :key="task.id" class="overview-video"><div class="overview-play">▶</div><div class="overview-video-copy"><strong>{{ task.title }}</strong><small>{{ task.mode }} · {{ task.collectedAt }}</small></div><span class="overview-metric">{{ task.progress }}%</span></div></section><section class="overview-card overview-status"><div class="overview-card-head"><div><h2>处理概况</h2><p>各处理环节的当前状态</p></div></div><div class="overview-status-row"><span class="status-symbol violet">◖</span><div><strong>视频采集</strong><small>采集视频信息与媒体文件</small></div><b>{{ tasks.length }} 条</b></div><div class="overview-status-row"><span class="status-symbol blue">☷</span><div><strong>评论采集</strong><small>评论与回复采集完成</small></div><b>{{ tasks.filter(t => t.commentsState === '已采集').length }} 条</b></div><div class="overview-status-row"><span class="status-symbol green">≋</span><div><strong>语音转写</strong><small>已生成转写内容</small></div><b>{{ tasks.filter(t => t.transcriptState === '已转写').length }} 条</b></div><button class="overview-link" @click="page = 'tasks'">前往任务列表 <span>→</span></button></section></div>
      </section>

      <section v-else-if="page === 'tasks'" class="page-content">
        <div class="page-heading"><div><div class="eyebrow">WORKSPACE <span>·</span> 任务中心</div><h1>任务列表</h1><p>查看任务进度、状态和错误信息。</p></div><button class="primary-button" @click="openNewTask"><span>＋</span> 新建任务</button></div>

        <section class="search-panel task-search-panel">
          <div class="search-main">
            <label class="search-box title-search"><span>⌕</span><input v-model="filters.videoName" placeholder="任务名称" @keyup.enter="applyFilters" /></label>
            <div ref="taskDatePicker" class="video-date-picker" @keydown.esc.stop="taskDateDropdownOpen = false"><button type="button" class="author-picker-trigger" :aria-expanded="taskDateDropdownOpen" aria-controls="task-date-menu" :title="taskDateLabel" @click="taskDateDropdownOpen = !taskDateDropdownOpen"><svg viewBox="0 0 20 20" width="15" height="15" fill="none" aria-hidden="true"><rect x="3" y="4" width="14" height="13" rx="2" stroke="currentColor" stroke-width="1.4"/><path d="M6 2v4m8-4v4M3 9h14" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg><span class="author-selection-text">{{ taskDateLabel }}</span><span>⌄</span></button><div v-if="taskDateDropdownOpen" id="task-date-menu" class="video-date-menu"><div class="date-menu-heading"><strong>任务创建时间</strong><button type="button" @click="filters.collectedFrom = ''; filters.collectedTo = ''; taskDateDropdownOpen = false; applyFilters()">清空</button></div><div class="date-menu-fields"><label>开始日期<input id="task-created-from" v-model="filters.collectedFrom" type="date" :max="filters.collectedTo || undefined" @change="applyFilters" /></label><span>至</span><label>结束日期<input v-model="filters.collectedTo" type="date" :min="filters.collectedFrom || undefined" @change="applyFilters" /></label></div><div class="date-menu-shortcuts"><button type="button" @click="setDateRange('today')">今天</button><button type="button" @click="setDateRange('yesterday')">昨天</button><button type="button" @click="setDateRange('week')">近一周</button><button type="button" @click="setDateRange('month')">近一月</button></div></div></div>
            <div ref="taskStatusPicker" class="task-status-picker" @keydown="moveTaskStatus" @keydown.esc.stop="taskStatusOpen = false"><button type="button" class="author-picker-trigger task-status-trigger" aria-label="任务状态" aria-haspopup="listbox" aria-controls="task-status-options" :aria-expanded="taskStatusOpen" @click="taskStatusOpen = !taskStatusOpen"><span class="status-option-dot" :class="taskStatusOptions.find((option) => option.label === taskFilter)?.tone"></span><span class="author-selection-text">{{ taskFilter }}</span><svg width="15" height="15" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m6 8 4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg></button><div v-if="taskStatusOpen" id="task-status-options" class="task-status-menu" role="listbox" aria-label="任务状态"><button v-for="option in taskStatusOptions" :key="option.label" type="button" role="option" :aria-selected="taskFilter === option.label" :class="{ selected: taskFilter === option.label }" @click="chooseTaskStatus(option.label)"><span class="status-option-dot" :class="option.tone"></span><span>{{ option.label }}</span><svg v-if="taskFilter === option.label" width="15" height="15" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m5 10 3 3 7-7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg></button></div></div>
            <button class="primary-button query-button" @click="applyFilters">查询</button>
          </div>
          <div class="search-foot"><span>可按任务名称、创建时间和状态筛选</span><div><span>已找到 <b>{{ visibleTasks.length }}</b> 条</span><button class="clear-filters" @click="clearFilters">重置条件</button></div></div>
        </section>

        <section v-if="showCreate" class="modal-backdrop" @click.self="!editSaving && closeTaskForm()"><div class="create-panel" role="dialog" aria-modal="true" aria-labelledby="create-title"><div class="panel-heading"><div><span class="eyebrow">{{ editTarget ? 'EDIT TASK' : 'NEW TASK' }}</span><h2 id="create-title">{{ editTarget ? '修改采集任务' : '创建采集任务' }}</h2><p>{{ editTarget ? '调整内容来源、处理模块和本次任务配置。' : '选择内容来源和处理模块。' }}</p></div><button class="close-button" aria-label="关闭" @click="closeTaskForm" :disabled="editSaving">×</button></div>
          <div class="form-section"><label class="field-label task-name-label" for="task-name">采集任务名称<span>必填</span></label><div class="url-input"><span>✎</span><input id="task-name" v-model="taskName" maxlength="80" placeholder="例如：10月竞品账号采集" /></div><div class="field-hint">任务名称会显示在列表中，并可用于搜索。</div></div>
          <div class="form-section"><div class="section-title"><span class="step-number">1</span><div><strong>{{ transcribeOnly ? '选择本地视频' : '选择采集来源' }}</strong><small>{{ transcribeOnly ? '仅转写时使用本机已有的视频文件' : '告诉我们要从哪里获取内容' }}</small></div></div><div v-if="!transcribeOnly" class="choice-grid"><button v-for="item in modeOptions" :key="item.id" class="choice-card" :class="{ selected: activeMode === item.id }" @click="activeMode = item.id"><span class="choice-icon">{{ item.icon }}</span><span class="choice-copy"><strong>{{ item.title }}</strong><small>{{ item.desc }}</small></span><span class="radio-dot"></span></button></div>
            <label class="field-label" for="source-url">{{ transcribeOnly ? '本地视频绝对路径' : activeMode === 'video' ? '视频链接' : activeMode === 'collection' ? '合集链接' : '作者主页链接' }}<span>必填</span></label><div class="url-input"><span>{{ transcribeOnly ? '▣' : '↗' }}</span><input id="source-url" v-model="sourceUrl" :placeholder="transcribeOnly ? '例如：D:\\videos\\sample.mp4' : '粘贴抖音链接或分享文案'" /><button v-if="sourceUrl" class="clear-input" @click="sourceUrl = ''">×</button></div><div class="field-hint">{{ transcribeOnly ? '必须使用存在且可读的本地文件绝对路径。' : '支持粘贴完整分享文案，系统会识别其中的抖音链接。' }}</div>
          </div>
          <div class="form-section"><div class="section-title"><span class="step-number">2</span><div><strong>选择处理模块</strong><small>四项默认全选，可按任务需要关闭</small></div></div><div class="module-grid"><button v-for="item in moduleOptions" :key="item.id" class="module-card" :class="{ selected: activeModules.includes(item.id) }" @click="toggleModule(item.id)"><span class="module-check">{{ activeModules.includes(item.id) ? '✓' : '' }}</span><strong>{{ item.title }}</strong><small>{{ item.desc }}</small></button></div><div v-if="activeModules.includes('transcribe')" class="transcribe-notice"><span>i</span>{{ transcribeOnly ? '仅转写文字时，需要填写本地视频的绝对路径。' : '转写与其他模块同时使用时，需要同时选择“下载视频”。' }}</div></div>
          <div class="form-section temp-section"><div class="temp-copy"><div class="temp-icon">⚙</div><div><strong>本次任务配置</strong><small>仅对本次任务生效，不改变全局配置</small></div></div><button class="switch" :class="{ on: tempConfig }" role="switch" :aria-checked="tempConfig" @click="tempConfig = !tempConfig"><span></span></button><div v-if="tempConfig" class="temp-editor"><p>勾选配置项后可修改数值；上限留空表示不限制，未勾选的项使用全局配置。</p><div v-for="option in taskConfigOptions" :key="option.key" class="temp-config-row"><label class="temp-option"><input v-model="selectedSettings" type="checkbox" :value="option.key" /><span>{{ option.label }}</span></label><label v-if="option.kind === 'toggle'" class="task-config-toggle"><input v-model="taskConfig[option.key]" type="checkbox" :disabled="!selectedSettings.includes(option.key)" :aria-label="option.label" />启用</label><input v-else v-model="taskConfig[option.key]" type="number" min="1" step="1" placeholder="不限制" :disabled="!selectedSettings.includes(option.key)" :aria-label="option.label" /></div></div></div>
          <div class="create-footer"><span><i class="status-dot"></i>{{ editTarget ? '保存后下次执行时生效' : '任务将在本机队列中处理' }}</span><div><button class="secondary-button" @click="closeTaskForm" :disabled="editSaving">取消</button><button class="primary-button" :disabled="editSaving" @click="createTask">{{ editSaving ? '保存中…' : editTarget ? '保存修改' : '开始任务' }} <span>→</span></button></div></div>
        </div></section>

        <section class="task-panel"><div class="task-panel-head"><div><h2>最近任务</h2><p>按创建时间倒序排列</p></div><span class="recent-count">{{ visibleTasks.length }} 条记录</span></div>
          <div class="batch-toolbar"><label class="batch-select-all"><input type="checkbox" :disabled="batchBusy || !visibleTasks.length" :checked="!!visibleTasks.length && selectedTaskIds.length === visibleTasks.length" :indeterminate="selectedTaskIds.length > 0 && selectedTaskIds.length < visibleTasks.length" @change="toggleAllTasks(($event.target as HTMLInputElement).checked)" />全选</label><span>已选 {{ selectedTaskIds.length }} 条</span><select v-model="batchTaskAction" :disabled="batchBusy" aria-label="任务批量操作"><option value="update">批量更新</option><option value="retry">批量重试</option><option value="delete">批量删除</option></select><button :disabled="batchBusy || !selectedTaskIds.length" :class="{ danger: batchTaskAction === 'delete' }" @click="requestBatch('tasks')">{{ batchBusy ? '提交中…' : '执行' }}</button><button class="batch-clear" :disabled="batchBusy || !selectedTaskIds.length" @click="selectedTaskIds = []">取消选择</button><small>更新仅适用于合集和作者任务</small></div><div class="task-table-wrap"><table class="task-table"><thead><tr><th class="selection-cell">选择</th><th>任务</th><th>类型</th><th>创建时间</th><th>进度</th><th>状态</th><th>错误信息</th><th>操作</th></tr></thead><tbody><tr v-for="task in visibleTasks" :key="task.id" :class="{ 'batch-selected': selectedTaskIds.includes(task.id) }"><td class="selection-cell"><input v-model="selectedTaskIds" type="checkbox" :value="task.id" :disabled="batchBusy" :aria-label="`选择任务 ${task.title}`" /></td><td><div class="task-name"><div class="task-type-icon" :class="task.mode === '指定作者' ? 'author' : task.mode === '短视频合集' ? 'collection' : ''">{{ task.mode === '指定作者' ? '◎' : task.mode === '短视频合集' ? '▤' : '▶' }}</div><div class="video-title clickable-video" role="button" tabindex="0" @click.stop="openTaskVideos(task)" @keydown.enter.stop="openTaskVideos(task)"><strong>{{ task.title }}</strong><small>{{ task.id }} · {{ task.collectedAt }}</small></div><button class="bookmark-button" :class="{ bookmarked: task.inReadingList }" :aria-label="task.inReadingList ? '取消标记' : '标记任务'" :title="task.inReadingList ? '已标记' : '标记任务'" @click.stop="toggleReading(task)"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.75 4.75h10.5a1 1 0 0 1 1 1v14l-6.25-3.8-6.25 3.8v-14a1 1 0 0 1 1-1Z" /></svg></button></div></td><td><div class="author-cell">{{ task.mode }}</div></td><td class="time-cell">{{ formatDateTime(task.createdAt) }}</td><td class="metric-cell">{{ task.progress }}%</td><td>{{ task.state }}</td><td><button v-if="task.transcript" class="task-error-preview" title="点击查看完整错误" @click.stop="errorTarget = task">{{ task.transcript.length > 36 ? task.transcript.slice(0, 36) + '…' : task.transcript }}</button><span v-else>—</span></td><td><div class="row-actions"><button v-if="task.state === '运行中' || task.state === '待处理' || task.state === '已暂停'" class="task-retry-inline" :disabled="pausingTasks.includes(task.id)" @click.stop="pauseOrResume(task)"><span>{{ task.state === '已暂停' ? '▶' : 'Ⅱ' }}</span><small>{{ task.state === '已暂停' ? '继续' : '暂停' }}</small></button><button class="task-retry-inline" :disabled="task.state === '运行中' || task.state === '待处理' || !!task.options.video_action" :title="task.options.video_action ? '视频单项操作请从视频列表重新发起' : task.state === '运行中' || task.state === '待处理' ? '先暂停任务再修改' : '修改任务及采集配置'" @click.stop="editTask(task)"><span>✎</span><small>修改</small></button><button v-if="task.mode === '短视频合集' || task.mode === '指定作者'" class="task-update-inline" :disabled="updatingTasks.includes(task.id) || retryingTasks.includes(task.id) || task.state === '运行中' || task.state === '待处理'" title="重新检查作品列表，只采集未入库视频" :aria-label="`增量更新任务 ${task.title}`" @click.stop="updateTask(task)"><span>⟳</span><small>{{ updatingTasks.includes(task.id) ? '提交中' : '更新' }}</small></button><button class="task-retry-inline" :disabled="retryingTasks.includes(task.id) || updatingTasks.includes(task.id) || task.state === '运行中' || task.state === '待处理'" :aria-label="`重试任务 ${task.title}`" :title="task.state === '运行中' || task.state === '待处理' ? '任务正在处理中' : '在原任务上重新执行'" @click.stop="retryTask(task)"><span>↻</span><small>{{ retryingTasks.includes(task.id) ? '提交中' : '重试' }}</small></button><button class="task-delete-inline" aria-label="删除任务" title="删除任务" @click.stop="deleteTarget = task"><span>⌫</span><small>删除</small></button></div></td></tr><tr v-if="visibleTasks.length === 0"><td colspan="8" class="empty-state">没有符合条件的任务</td></tr></tbody></table></div><div class="table-footer"><span>显示最近 <b>{{ visibleTasks.length }}</b> 条任务</span><div class="pagination"><button disabled>‹</button><button class="current-page">1</button><button disabled>›</button></div></div>
        </section>
        <div class="demo-note"><span>i</span><div><strong>本机任务队列</strong><p>任务状态保存在 SQLite；服务重启后仍可查看和重试。</p></div><button @click="page = 'settings'">前往配置 <span>→</span></button></div>
      </section>

      <section v-else-if="page === 'videos'" class="page-content videos-page">
        <div class="page-heading"><div><div class="eyebrow">CONTENT <span>·</span> 采集结果</div><h1>采集视频</h1><p>查看视频基础信息、互动数据、处理状态和评论内容。</p></div><button class="primary-button" @click="openNewTask"><span>＋</span> 新建采集</button></div>
        <section class="search-panel video-search-panel"><div class="video-search-main"><div ref="authorPicker" class="author-picker" @keydown.esc.stop="authorDropdownOpen = false"><button type="button" class="author-picker-trigger" :aria-expanded="authorDropdownOpen" aria-controls="author-options-menu" @click="toggleAuthorDropdown"><span class="author-selection-text">{{ selectedAuthorNames.length ? selectedAuthorNames.join('、') : '选择作者名称（支持多选）' }}</span><span v-if="selectedAuthorNames.length" class="author-selection-count">{{ selectedAuthorNames.length }}</span><span>⌄</span></button><div v-if="authorDropdownOpen" id="author-options-menu" class="author-options-menu"><input v-model="authorSearch" class="author-options-search" aria-label="搜索作者名称" placeholder="搜索作者名称…" /><div class="author-options-toolbar"><span>已选 {{ selectedAuthorNames.length }} 位作者</span><button type="button" @click="selectedAuthorNames = []; refreshVideos()">清空</button></div><div class="author-options-list"><p v-if="authorOptionsLoading">正在加载…</p><p v-else-if="authorOptionsFailed">作者列表加载失败 <button type="button" @click="loadAuthorOptions">重试</button></p><p v-else-if="!authorOptions.length">没有匹配的作者</p><label v-for="option in authorOptionsLoading || authorOptionsFailed ? [] : authorOptions" :key="option.name" :class="{ selected: selectedAuthorNames.includes(option.name) }"><input v-model="selectedAuthorNames" type="checkbox" :value="option.name" @change="refreshVideos" /><span class="dropdown-option-avatar" aria-hidden="true">{{ option.name.slice(0, 1) }}</span><span class="dropdown-option-name">{{ option.name }}</span><small>{{ option.count }} 条视频</small></label></div></div></div><div ref="tagPicker" class="author-picker" @keydown.esc.stop="tagDropdownOpen = false"><button type="button" class="author-picker-trigger" :aria-expanded="tagDropdownOpen" aria-controls="tag-options-menu" @click="toggleTagDropdown"><span class="author-selection-text">{{ selectedTags.length ? selectedTags.join('、') : '选择视频标签（支持多选）' }}</span><span v-if="selectedTags.length" class="author-selection-count">{{ selectedTags.length }}</span><span>⌄</span></button><div v-if="tagDropdownOpen" id="tag-options-menu" class="author-options-menu"><input v-model="tagSearch" class="author-options-search" aria-label="搜索视频标签" placeholder="搜索视频标签…" /><div class="author-options-toolbar"><span>已选 {{ selectedTags.length }} 个标签</span><button type="button" @click="selectedTags = []; refreshVideos()">清空</button></div><div class="author-options-list"><p v-if="tagOptionsLoading">正在加载…</p><p v-else-if="tagOptionsFailed">标签列表加载失败 <button type="button" @click="loadTagOptions">重试</button></p><p v-else-if="!tagOptions.length">没有匹配的标签</p><label v-for="option in tagOptionsLoading || tagOptionsFailed ? [] : tagOptions" :key="option.name" :class="{ selected: selectedTags.includes(option.name) }"><input v-model="selectedTags" type="checkbox" :value="option.name" @change="refreshVideos" /><span class="dropdown-option-name">#{{ option.name }}</span><small>{{ option.count }} 条</small></label></div></div></div><label class="search-box video-search-box"><span>⌕</span><input v-model="videoSearch.title" placeholder="视频标题或简介关键词" @keyup.enter="refreshVideos" /></label><div ref="datePicker" class="video-date-picker" @keydown.esc.stop="dateDropdownOpen = false"><button type="button" class="author-picker-trigger" :aria-expanded="dateDropdownOpen" aria-controls="video-date-menu" :title="videoDateLabel" @click="dateDropdownOpen = !dateDropdownOpen"><svg viewBox="0 0 20 20" width="15" height="15" fill="none" aria-hidden="true"><rect x="3" y="4" width="14" height="13" rx="2" stroke="currentColor" stroke-width="1.4"/><path d="M6 2v4m8-4v4M3 9h14" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg><span class="author-selection-text">{{ videoDateLabel }}</span><span>⌄</span></button><div v-if="dateDropdownOpen" id="video-date-menu" class="video-date-menu"><div class="date-menu-heading"><strong>视频发布时间</strong><button type="button" @click="clearVideoDates">全部时间</button></div><div class="date-menu-fields"><label>开始日期<input id="video-published-from" v-model="videoSearch.publishedFrom" type="date" :max="videoSearch.publishedTo || undefined" @change="refreshVideos" /></label><span>至</span><label>结束日期<input v-model="videoSearch.publishedTo" type="date" :min="videoSearch.publishedFrom || undefined" @change="refreshVideos" /></label></div><div class="date-menu-shortcuts"><button type="button" @click="setVideoDateRange('today')">今天</button><button type="button" @click="setVideoDateRange('yesterday')">昨天</button><button type="button" @click="setVideoDateRange('week')">近一周</button><button type="button" @click="setVideoDateRange('month')">近一月</button></div></div></div><button class="primary-button query-button" @click="refreshVideos">查询</button></div><div class="search-foot"><span>可按作者、标签、视频标题和发布时间搜索 · {{ videoDateLabel }}</span><div><span v-if="videosLoading">查询中…</span><span v-else>已找到 <b>{{ videoTotal }}</b> 条</span><button class="clear-filters" @click="resetVideoSearch">重置条件</button></div></div></section>
        <div class="batch-toolbar video-batch-toolbar"><label class="batch-select-all"><input type="checkbox" :disabled="batchBusy || !videos.length" :checked="!!videos.length && selectedVideoIds.length === videos.length" :indeterminate="selectedVideoIds.length > 0 && selectedVideoIds.length < videos.length" @change="toggleAllVideos(videos, ($event.target as HTMLInputElement).checked)" />全选视频</label><span>已选 {{ selectedVideoIds.length }} 条</span><select v-model="batchVideoAction" :disabled="batchBusy" aria-label="视频批量操作"><option value="refresh">批量更新</option><option value="download">批量下载</option><option value="comments">批量抓取评论</option><option value="transcribe">批量转写</option><option value="report">批量生成报告</option><option value="delete">批量删除</option></select><button :disabled="batchBusy || !selectedVideoIds.length" :class="{ danger: batchVideoAction === 'delete' }" @click="requestBatch('videos')">{{ batchBusy ? '提交中…' : '执行' }}</button><button class="batch-clear" :disabled="batchBusy || !selectedVideoIds.length" @click="selectedVideoIds = []">取消选择</button></div><div class="author-groups-heading"><h2>按作者归类</h2><span>{{ authorVideoGroups.length }} 位作者 · {{ videoTotal }} 条视频</span></div>
        <div v-if="videoLoadError" class="task-panel empty-state">视频读取失败：{{ videoLoadError }} <button class="text-link" @click="refreshVideos">重新加载</button></div><div v-else-if="!videos.length" class="task-panel empty-state"><template v-if="videosLoading">正在查询视频…</template><template v-else><p>暂无符合当前筛选条件的视频数据</p><p v-if="videoSearch.publishedFrom || videoSearch.publishedTo">当前按发布时间筛选：{{ videoDateLabel }}，较早发布的视频不会显示。</p><div class="empty-filter-actions"><button v-if="videoSearch.publishedFrom || videoSearch.publishedTo" class="secondary-button" @click="clearVideoDates">查看全部发布时间</button><button v-if="videoSourceUrl" class="secondary-button" @click="clearVideoSource">移除来源限制</button><button class="secondary-button" @click="resetVideoSearch">重置条件</button></div></template></div>
        <details v-for="group in authorVideoGroups" :key="group.key" class="task-panel video-list-panel author-video-group" open><summary class="author-group-summary"><div class="author-group-avatar">{{ group.name.slice(0, 1) }}</div><div class="author-group-info"><h2>{{ group.name }}</h2><p>{{ group.authorId || '暂无作者 ID' }}</p></div><span class="recent-count">{{ group.videos.length }} 条视频</span><span class="author-group-chevron" aria-hidden="true"><svg viewBox="0 0 20 20" fill="none"><path d="m6 8 4 4 4-4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" /></svg></span></summary><div class="task-table-wrap"><table class="task-table video-table"><thead><tr><th class="selection-cell"><input type="checkbox" :disabled="batchBusy" :checked="group.videos.every((video) => selectedVideoIds.includes(videoKey(video)))" :indeterminate="group.videos.some((video) => selectedVideoIds.includes(videoKey(video))) && !group.videos.every((video) => selectedVideoIds.includes(videoKey(video)))" :aria-label="`全选 ${group.name} 的视频`" @change="toggleAllVideos(group.videos, ($event.target as HTMLInputElement).checked)" /></th><th>视频</th><th>标签</th><th>发布时间</th><th>点赞</th><th>收藏</th><th>转发</th><th>处理状态</th><th>操作</th></tr></thead><tbody><tr v-for="video in group.videos" :key="`${video.platform}-${video.video_id}`" :class="{ 'batch-selected': selectedVideoIds.includes(videoKey(video)) }"><td class="selection-cell"><input v-model="selectedVideoIds" type="checkbox" :value="videoKey(video)" :disabled="batchBusy" :aria-label="`选择视频 ${video.title}`" /></td><td><div class="collected-video clickable-video" role="button" tabindex="0" @click="openVideoDetail(video)" @keydown.enter="openVideoDetail(video)"><div class="video-cover"><img v-if="video.cover_url" :src="video.cover_url" alt="" /><span v-else>{{ video.content_type === 'image' ? '▧' : '▶' }}</span></div><div><strong>{{ video.title || '标题未读取' }}</strong><small>{{ video.description || video.video_id }}</small></div></div></td><td class="video-tags-cell"><div v-if="video.hashtags.length" class="video-list-tags"><span v-for="tag in video.hashtags" :key="tag">#{{ tag }}</span></div><span v-else class="detail-muted">暂无标签</span></td><td class="time-cell">{{ formatDateTime(video.published_at) }}</td><td class="metric-cell">{{ formatCount(video.likes) }}</td><td class="metric-cell">{{ formatCount(video.favorites) }}</td><td class="metric-cell">{{ formatCount(video.shares) }}</td><td><div class="video-process-grid"><button v-for="action in videoActions" :key="action.key" class="video-process-button" :class="{ complete: videoActionDone(video, action.key), busy: videoActionBusy(video, action.key) }" :disabled="(videoActionDone(video, action.key) && action.key !== 'report') || Boolean(video.running_actions?.length) || submittingVideoActions.some((key) => key.startsWith(video.video_id + ':')) || (action.key === 'transcribe' && video.content_type === 'image')" :title="action.key === 'report' && video.report_complete ? '打开所在目录并选中报告文件' : action.key === 'transcribe' && video.content_type === 'image' ? '图文作品不支持语音转写' : videoActionDone(video, action.key) ? action.done : '点击执行：' + action.pending" @click.stop="startVideoAction(video, action.key)"><span>{{ videoActionBusy(video, action.key) ? '…' : videoActionDone(video, action.key) ? '✓' : action.icon }}</span>{{ videoActionBusy(video, action.key) ? action.label + '中' : action.key === 'transcribe' && video.content_type === 'image' ? '不适用' : action.label }}</button></div></td><td><div class="video-row-actions"><button class="video-refresh-button" title="更新点赞、收藏、转发和评论" :disabled="Boolean(video.running_actions?.length) || submittingVideoActions.some((key) => key.startsWith(video.video_id + ':'))" @click.stop="startVideoAction(video, 'refresh')"><span aria-hidden="true">⟳</span><small>{{ videoActionBusy(video, 'refresh') ? '更新中…' : '更新' }}</small></button><button class="video-delete-button" :aria-label="`删除视频记录 ${video.title || video.video_id}`" @click.stop="deleteVideoTarget = video"><span aria-hidden="true">⌫</span><small>删除</small></button></div></td></tr></tbody></table></div></details>
        <section v-if="selectedVideo" class="modal-backdrop" @click.self="selectedVideo = null" @keydown.esc="selectedVideo = null">
          <div class="create-panel video-detail-panel" role="dialog" aria-modal="true" aria-labelledby="video-detail-title">
            <div class="panel-heading"><div><span class="eyebrow">VIDEO DETAIL</span><h2 id="video-detail-title">{{ selectedVideo.title || '视频详情' }}</h2></div><button class="close-button" aria-label="关闭" @click="selectedVideo = null">×</button></div>
            <div class="video-detail-body">
              <div class="detail-player">
                <video v-if="selectedVideo.download_complete && selectedVideo.content_type !== 'image'" :key="selectedVideo.video_id" controls playsinline preload="metadata" :poster="selectedVideo.cover_url || undefined" :src="`${API_BASE}/api/videos/${encodeURIComponent(selectedVideo.platform)}/${encodeURIComponent(selectedVideo.video_id)}/media`" @error="videoPlaybackError = true">浏览器不支持视频播放。</video>
                <div v-else class="detail-player-empty"><img v-if="selectedVideo.cover_url" :src="selectedVideo.cover_url" alt="作品封面" /><p>{{ selectedVideo.content_type === 'image' ? '图文作品暂无视频可播放' : '视频尚未下载' }}</p><button v-if="selectedVideo.content_type !== 'image'" class="secondary-button" :disabled="Boolean(selectedVideo.running_actions?.length)" @click="startVideoAction(selectedVideo, 'download')">下载视频</button></div>
              </div>
              <p v-if="videoPlaybackError" class="playback-error">视频加载失败，请确认本地文件仍存在且浏览器支持该视频格式。</p>
              <div class="detail-reading-grid">
                <section class="detail-section transcript-section"><h3>转写文字</h3><p v-if="selectedVideo.transcript">{{ selectedVideo.transcript }}</p><p v-else class="detail-muted">暂无转写文字</p><p v-if="selectedVideo.transcript_error" class="playback-error">{{ selectedVideo.transcript_error }}</p></section>
                <section class="detail-section"><div class="comment-section-heading"><h3>评论 <span class="detail-muted">{{ selectedVideoComments.length }} 条</span></h3><button type="button" class="marked-comments-filter" :class="{ active: markedCommentsOnly }" :aria-pressed="markedCommentsOnly" @click="markedCommentsOnly = !markedCommentsOnly">{{ markedCommentsOnly ? '查看全部评论' : '只看已标记' }}<span>{{ markedCommentCount }}</span></button></div><p v-if="videoCommentsLoading" class="detail-muted">正在读取评论…</p><p v-else-if="visibleVideoComments.length === 0" class="detail-muted">{{ markedCommentsOnly ? '暂无已标记评论' : '暂无已采集评论' }}</p><div v-else class="comment-list"><article v-for="comment in visibleVideoComments" :key="comment.comment_id" :class="{ reply: comment.parent_id, marked: comment.marked }"><div><strong>{{ comment.author || '匿名用户' }}</strong><div class="comment-meta-actions"><span>赞 {{ formatCount(comment.likes) }}</span><button type="button" class="comment-mark-button" :class="{ active: comment.marked }" :aria-pressed="comment.marked" :disabled="markingComments.includes(comment.comment_id)" @click="toggleCommentMark(comment)">{{ markingComments.includes(comment.comment_id) ? '保存中…' : comment.marked ? '★ 已标记' : '☆ 标记' }}</button></div></div><p>{{ comment.text }}</p></article></div></section>
              </div>
              <details class="detail-extra" :key="selectedVideo.video_id"><summary>其他信息 · 简介、作者、互动数据及文件</summary><div class="video-detail-summary"><dl><div><dt>作者</dt><dd>{{ selectedVideo.author_name || '—' }}</dd></div><div><dt>发布时间</dt><dd>{{ formatDateTime(selectedVideo.published_at) }}</dd></div><div><dt>视频时长</dt><dd>{{ selectedVideo.duration_seconds ? `${selectedVideo.duration_seconds} 秒` : '—' }}</dd></div><div><dt>点赞 / 收藏 / 转发</dt><dd>{{ formatCount(selectedVideo.likes) }} / {{ formatCount(selectedVideo.favorites) }} / {{ formatCount(selectedVideo.shares) }}</dd></div><div><dt>处理状态</dt><dd>{{ processingLabel(selectedVideo) }}</dd></div></dl></div><section class="detail-section"><h3>视频简介</h3><p>{{ selectedVideo.description || '暂无简介' }}</p><div class="tag-list"><span v-for="tag in selectedVideo.hashtags" :key="tag">#{{ tag }}</span></div></section><section class="detail-section detail-paths"><h3>文件与链接</h3><p><b>作品链接：</b><a :href="selectedVideo.url" target="_blank" rel="noreferrer">{{ selectedVideo.url }}</a></p><p><b>本地文件：</b>{{ selectedVideo.local_media_path || '未下载' }}</p></section></details>
            </div>
          </div>
        </section>
      </section>

      <div v-if="deleteVideoTarget" class="modal-backdrop confirm-backdrop" @click.self="deleteVideoTarget = null"><section class="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="delete-video-title"><div class="confirm-icon">!</div><h2 id="delete-video-title">删除这条视频记录？</h2><p>“{{ deleteVideoTarget.title || deleteVideoTarget.video_id }}”及其关联的评论和转写分段会被删除，任务记录不受影响。</p><div class="confirm-actions"><button class="secondary-button" @click="deleteVideoTarget = null">取消</button><button class="danger-button" @click="confirmDeleteVideo">确认删除</button></div></section></div>
      <section v-if="page === 'settings'" class="page-content settings-page">
        <div class="page-heading"><div><div class="eyebrow">PREFERENCES <span>·</span> 配置管理</div><h1>采集配置</h1><p>按功能管理所有运行参数，新建任务时可作为默认配置使用。</p></div><div class="heading-actions"><button class="secondary-button" @click="resetSettings">恢复默认</button><button class="primary-button" @click="saveSettings">保存配置 <span>✓</span></button></div></div>
        <div class="settings-layout"><aside class="settings-nav"><div class="settings-nav-title">配置分区 <span>{{ groups.length }}</span></div><button v-for="group in groups" :key="group.id" class="settings-nav-item" :class="{ selected: settingsGroup === group.id }" @click="settingsGroup = group.id"><span class="group-icon">{{ group.icon }}</span><span class="group-copy"><strong>{{ group.label }}</strong><small>{{ group.caption }}</small></span><span v-if="settingsGroup === group.id" class="nav-current"></span></button><div class="coverage-note"><span>✓</span><div><strong>配置项已覆盖</strong><small>{{ settingCount }} 项 · 与示例配置对应</small></div></div></aside>
          <div class="settings-content"><div class="settings-content-head"><div><div class="eyebrow">CONFIGURATION</div><h2>{{ activeGroup.label }}</h2><p>{{ activeGroup.caption }}<span class="config-count">{{ visibleSettings.length }} 项配置</span></p></div><label class="search-box settings-search"><span>⌕</span><input v-model="settingsSearch" placeholder="搜索当前分区" /></label></div><div class="settings-card"><div v-for="item in visibleSettings" :key="item.key" class="setting-row" :class="{ 'textarea-row': item.kind === 'textarea' }"><div class="setting-info"><label :for="item.key">{{ item.label }}</label><small>{{ item.description }}</small></div><div class="setting-control" :class="{ 'toggle-control': item.kind === 'toggle' }"><textarea v-if="item.kind === 'textarea'" :id="item.key" :value="stringValue(item.key)" rows="3" placeholder="每行一个链接，可留空" @input="setStringValue(item.key, $event)"></textarea><div v-else-if="item.kind === 'secret'" class="secret-field"><span class="secret-status" :class="{ configured: secretConfigured }" role="status">{{ secretConfigured ? '✓ 已配置' : '未配置' }}<small v-if="stringValue(item.key)"> · 保存后更新</small></span><div class="secret-input"><input :id="item.key" :value="stringValue(item.key)" :type="revealKey ? 'text' : 'password'" :placeholder="secretConfigured ? '已配置；留空表示保持不变' : '填写 MiMo API Key'" @input="setStringValue(item.key, $event)" /><button type="button" :disabled="keyLoading || (!secretConfigured && !stringValue(item.key))" @click="toggleApiKey">{{ keyLoading ? '读取中…' : revealKey ? '隐藏' : '查看' }}</button></div></div><select v-else-if="item.kind === 'select'" :id="item.key" :value="stringValue(item.key)" @change="setStringValue(item.key, $event)"><option v-for="option in item.options" :key="option" :value="option">{{ option }}</option></select><button v-else-if="item.kind === 'toggle'" :id="item.key" class="switch" :class="{ on: config[item.key] }" role="switch" :aria-checked="Boolean(config[item.key])" @click="config[item.key] = !config[item.key]"><span></span></button><div v-else class="value-input"><input :id="item.key" :value="stringValue(item.key)" :type="item.kind === 'number' ? 'number' : 'text'" :placeholder="item.kind === 'number' ? '不限制' : ''" @input="setStringValue(item.key, $event)" /><span v-if="item.unit">{{ item.unit }}</span></div><code>{{ item.key }}</code></div></div><div v-if="visibleSettings.length === 0" class="empty-settings">没有找到匹配的配置项</div></div><div class="settings-tip"><span>i</span><p>配置从本机后端读取，保存前会校验；已设置的 API Key 留空时会继续保留。</p></div></div>
        </div>
      </section>
    </main>
    <div v-if="errorTarget" class="modal-backdrop confirm-backdrop" @click.self="errorTarget = null" @keydown.esc="errorTarget = null"><section class="confirm-dialog error-dialog" role="dialog" aria-modal="true" aria-labelledby="task-error-title"><div class="panel-heading"><h2 id="task-error-title">任务错误详情</h2><button class="close-button" aria-label="关闭错误详情" autofocus @click="errorTarget = null">×</button></div><p>{{ errorTarget.title }}</p><pre class="task-error-full">{{ errorTarget.transcript }}</pre><button class="secondary-button" @click="errorTarget = null">关闭</button></section></div>
      <div v-if="deleteTarget" class="modal-backdrop confirm-backdrop" @click.self="deleteTarget = null"><section class="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="delete-title"><div class="confirm-icon">!</div><h2 id="delete-title">删除这条任务记录？</h2><p>“{{ deleteTarget.title }}”将从任务列表移除，已经采集的视频、评论和报告仍会保留。</p><div class="confirm-actions"><button class="secondary-button" @click="deleteTarget = null">取消</button><button class="danger-button" @click="confirmDelete">确认删除</button></div></section></div>
    <div v-if="batchConfirm" class="modal-backdrop confirm-backdrop" @click.self="batchConfirm = null"><section class="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="batch-delete-title"><h2 id="batch-delete-title">确认批量删除？</h2><p>将删除已选的 {{ batchConfirm === 'tasks' ? selectedTaskIds.length : selectedVideoIds.length }} 条{{ batchConfirm === 'tasks' ? '任务记录' : '视频记录及关联评论、转写和标记' }}。本地视频和报告文件会保留，运行中的项目会跳过。</p><div class="confirm-actions"><button class="secondary-button" @click="batchConfirm = null">取消</button><button class="danger-button" @click="executeBatch(batchConfirm, 'delete')">确认删除</button></div></section></div>
    <div v-if="batchResult" class="modal-backdrop confirm-backdrop" @click.self="batchResult = null"><section class="confirm-dialog batch-result-dialog" role="dialog" aria-modal="true" aria-labelledby="batch-result-title"><h2 id="batch-result-title">批量操作结果</h2><p>{{ batchResult.summary }}</p><p>已提交的流程将在后台逐项执行，具体进度和错误可在任务列表查看。</p><ul v-if="batchResult.details.length"><li v-for="(detail, index) in batchResult.details" :key="index">{{ detail }}</li></ul><button class="secondary-button" @click="batchResult = null">关闭</button></section></div>
    <Transition name="toast"><div v-if="toast" class="toast-message"><span>✓</span>{{ toast }}</div></Transition>
  </div>

</template>
