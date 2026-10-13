<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

type Page = 'overview' | 'tasks' | 'settings'
type TaskMode = 'video' | 'collection' | 'author'
type TaskModule = 'video' | 'comments'
type TaskState = '运行中' | '已完成' | '待处理' | '失败'
type TranscriptState = '未转写' | '转写中' | '已转写'
type CommentsState = '未采集' | '采集中' | '已采集'
type StepStatus = 'done' | 'failed' | 'pending' | 'running'
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
}

const page = ref<Page>('tasks')
const toast = ref('')
const showCreate = ref(false)
const activeMode = ref<TaskMode>('video')
const activeModules = ref<TaskModule[]>(['video'])
const sourceUrl = ref('')
const tempConfig = ref(false)
const selectedSettings = ref(['crawl.max_comments_per_video', 'inputs.max_videos_per_profile'])
const settingsGroup = ref('inputs')
const revealKey = ref(false)
const taskFilter = ref('全部任务')
const appliedTaskFilter = ref('全部任务')
const filtersOpen = ref(true)
const activeMenuTaskId = ref('')
const deleteTarget = ref<Task | null>(null)
const filters = reactive({ videoName: '', transcriptQuery: '', authorQuery: '', collectedFrom: '', collectedTo: '', publishedFrom: '', publishedTo: '', likesMin: '', likesMax: '', favoritesMin: '', favoritesMax: '', sharesMin: '', sharesMax: '' })
const appliedFilters = reactive({ ...filters })
const settingsSearch = ref('')

const config = reactive<Record<string, string | number | boolean>>({
  platform: 'douyin',
  'inputs.urls': '', 'inputs.urls_file': '', 'inputs.max_videos_per_profile': '', 'inputs.max_videos_per_collection': '',
  'browser.headless': false, 'browser.profile_dir': 'work/browser', 'browser.navigation_timeout_seconds': 30, 'browser.manual_wait_seconds': 0,
  'crawl.delay_seconds': 2, 'crawl.max_retries': 3, 'crawl.retry_backoff_seconds': 5, 'crawl.max_comments_per_video': '', 'crawl.no_new_content_scrolls': 3, 'crawl.resume': true, 'crawl.refresh_completed': false,
  'audio.ffmpeg_path': 'ffmpeg', 'audio.mp3_bitrate_kbps': 64, 'audio.download_timeout_seconds': 120, 'audio.keep_temporary_files': false,
  'download.output_dir': 'outputs/downloads',
  'asr.api_key': '', 'asr.base_url': 'https://api.xiaomimimo.com/v1', 'asr.model': 'mimo-v2.5-asr', 'asr.language': 'auto', 'asr.chunk_seconds': 180, 'asr.overlap_seconds': 2, 'asr.timeout_seconds': 120, 'asr.max_retries': 3, 'asr.output_dir': 'outputs/transcripts',
  'report.output_dir': 'outputs/reports', 'report.top_comments': '', 'report.min_comment_chars': 50, 'report.include_comment_author': false, 'report.write_partial_results': true,
  'runtime.state_db': 'work/state.sqlite3', 'runtime.log_file': 'work/run.log', 'runtime.log_level': 'INFO',
})

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
    { key: 'runtime.state_db', label: '状态数据库', description: '保存采集状态、评论和转写结果。' },
    { key: 'runtime.log_file', label: '日志文件', description: '记录任务进度与失败原因。' },
    { key: 'runtime.log_level', label: '日志详细程度', description: '控制日志记录级别。', kind: 'select', options: ['DEBUG', 'INFO', 'WARNING', 'ERROR'] },
  ],
}

const tasks = ref<Task[]>([
  { id: 'TK-20261013-004', title: '秋日咖啡馆探店｜城市漫游的一天', mode: '单个短视频', module: '只采集视频', input: 'douyin.com/video/72938•••••', state: '运行中', progress: 68, createdAt: '今天 14:32', collectedAt: '2026-10-13', publishedAt: '2026-10-12', author: '阿森的城市漫游', transcript: '这家藏在老街里的咖啡馆，周末一定要来试试手冲咖啡和桂花拿铁。', likes: 12800, favorites: 3260, shares: 842, transcriptState: '已转写', commentsState: '已采集', inReadingList: false, videoStatus: 'done', commentsStatus: 'running', transcriptStatus: 'done' },
  { id: 'TK-20261013-003', title: '设计灵感合集｜10个品牌视觉案例', mode: '短视频合集', module: '只采集评论', input: 'douyin.com/collection/72912•••••', state: '已完成', progress: 100, createdAt: '今天 13:08', collectedAt: '2026-10-13', publishedAt: '2026-10-08', author: '设计观察所', transcript: '今天分享十个品牌视觉案例，从字体、配色到包装设计，拆解背后的设计逻辑。', likes: 54200, favorites: 18600, shares: 4310, transcriptState: '已转写', commentsState: '已采集', inReadingList: true, videoStatus: 'done', commentsStatus: 'done', transcriptStatus: 'done' },
  { id: 'TK-20261013-002', title: '产品观察室｜AI工具效率实测', mode: '指定作者', module: '只采集视频', input: 'douyin.com/user/MS4wLjAB•••', state: '已完成', progress: 100, createdAt: '今天 11:46', collectedAt: '2026-10-13', publishedAt: '2026-09-30', author: '产品观察室', transcript: '实测了三款 AI 效率工具，最后一款可以把一小时的会议整理成行动清单。', likes: 8700, favorites: 5120, shares: 1290, transcriptState: '未转写', commentsState: '未采集', inReadingList: false, videoStatus: 'done', commentsStatus: 'pending', transcriptStatus: 'failed' },
  { id: 'TK-20261013-001', title: '本周热门选题｜普通人的理财误区', mode: '短视频合集', module: '只采集视频', input: 'douyin.com/collection/72901•••••', state: '失败', progress: 42, createdAt: '今天 10:15', collectedAt: '2026-10-13', publishedAt: '2026-10-01', author: '慢慢变富研究所', transcript: '很多人以为记账就等于理财，其实真正重要的是先建立自己的现金流意识。', likes: 23100, favorites: 9780, shares: 2870, transcriptState: '已转写', commentsState: '未采集', inReadingList: false, videoStatus: 'failed', commentsStatus: 'failed', transcriptStatus: 'pending' },
])

const modeOptions = [
  { id: 'video' as TaskMode, icon: '▶', title: '单个短视频', desc: '粘贴一个作品链接' },
  { id: 'collection' as TaskMode, icon: '▤', title: '短视频合集', desc: '按合集顺序处理作品' },
  { id: 'author' as TaskMode, icon: '◎', title: '指定作者', desc: '采集作者主页作品' },
]
const moduleOptions = [
  { id: 'video' as TaskModule, title: '只采集视频', desc: '获取视频信息与媒体' },
  { id: 'comments' as TaskModule, title: '只采集评论', desc: '采集作品评论与回复' },
]
const visibleTasks = computed(() => tasks.value.filter((task) => {
  const includes = (value: string, query: string) => !query.trim() || value.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())
  const matchesText = includes(task.title, appliedFilters.videoName) && includes(task.transcript, appliedFilters.transcriptQuery) && includes(task.author, appliedFilters.authorQuery)
  const inDate = (value: string, from: string, to: string) => (!from || value >= from) && (!to || value <= to)
  const inRange = (value: number, min: string, max: string) => (!min || value >= Number(min)) && (!max || value <= Number(max))
  return matchesText && (appliedTaskFilter.value === '全部任务' || task.state === appliedTaskFilter.value)
    && inDate(task.collectedAt, appliedFilters.collectedFrom, appliedFilters.collectedTo)
    && inDate(task.publishedAt, appliedFilters.publishedFrom, appliedFilters.publishedTo)
    && inRange(task.likes, appliedFilters.likesMin, appliedFilters.likesMax)
    && inRange(task.favorites, appliedFilters.favoritesMin, appliedFilters.favoritesMax)
    && inRange(task.shares, appliedFilters.sharesMin, appliedFilters.sharesMax)
}))
const activeGroup = computed(() => groups.find((group) => group.id === settingsGroup.value)!)
const visibleSettings = computed(() => (definitions[settingsGroup.value] || []).filter((item) => `${item.label}${item.description}${item.key}`.toLowerCase().includes(settingsSearch.value.toLowerCase())))
const settingCount = computed(() => Object.values(definitions).reduce((sum, items) => sum + items.length, 0))

function notify(message: string) {
  toast.value = message
  window.setTimeout(() => { if (toast.value === message) toast.value = '' }, 2400)
}
function createTask() {
  if (!sourceUrl.value.trim()) { notify('请先填写抖音链接'); return }
  const mode = modeOptions.find((item) => item.id === activeMode.value)!
  if (!activeModules.value.length) { notify('至少选择一个处理模块'); return }
  const module = activeModules.value.map((id) => moduleOptions.find((item) => item.id === id)!.title).join(' + ')
  const id = `TK-${new Date().toISOString().slice(0, 10).replaceAll('-', '')}-${String(tasks.value.length + 1).padStart(3, '0')}`
  tasks.value.unshift({ id, title: sourceUrl.value.trim().split('/').filter(Boolean).at(-1) || '新建采集任务', mode: mode.title, module, input: sourceUrl.value.trim().replace(/^https?:\/\//, ''), state: '运行中', progress: 8, createdAt: '刚刚', collectedAt: new Date().toISOString().slice(0, 10), publishedAt: '', author: '等待采集', transcript: '', likes: 0, favorites: 0, shares: 0, transcriptState: '未转写', commentsState: '未采集', inReadingList: false, videoStatus: 'running', commentsStatus: 'pending', transcriptStatus: 'pending' })
  showCreate.value = false
  sourceUrl.value = ''
  notify('任务已加入列表（模拟数据）')
  window.setTimeout(() => {
    const current = tasks.value.find((task) => task.id === id)
    if (current) { current.progress = 100; current.state = '已完成'; current.videoStatus = 'done'; if (activeModules.value.includes('comments')) { current.commentsStatus = 'done'; current.commentsState = '已采集' } }
  }, 7000)
}
function toggleModule(id: TaskModule) {
  activeModules.value = activeModules.value.includes(id) ? activeModules.value.filter((module) => module !== id) : [...activeModules.value, id]
}
function transcribe(task: Task) {
  if (task.transcriptState === '转写中') return
  task.transcriptState = '转写中'
  task.transcriptStatus = 'running'
  activeMenuTaskId.value = ''
  notify(`“${task.title}”已加入转写队列（模拟）`)
  window.setTimeout(() => {
    task.transcriptState = '已转写'
    task.transcriptStatus = 'done'
    if (!task.transcript) task.transcript = '模拟转写已完成，可在后续版本中查看转写正文。'
  }, 4500)
}
function pullComments(task: Task) {
  if (task.commentsState === '采集中') return
  task.commentsState = '采集中'
  task.commentsStatus = 'running'
  activeMenuTaskId.value = ''
  notify(`“${task.title}”已加入评论采集队列（模拟）`)
  window.setTimeout(() => { task.commentsState = '已采集'; task.commentsStatus = 'done' }, 4500)
}
function statusLabel(label: string, status: StepStatus) {
  return `${label}${status === 'done' ? '已完成' : status === 'failed' ? '失败' : status === 'running' ? '处理中' : '未处理'}`
}
function toggleReading(task: Task) {
  task.inReadingList = !task.inReadingList
  activeMenuTaskId.value = ''
  notify(task.inReadingList ? '已加入阅读' : '已从阅读列表移除')
}
function confirmDelete() {
  if (!deleteTarget.value) return
  const title = deleteTarget.value.title
  tasks.value = tasks.value.filter((task) => task.id !== deleteTarget.value?.id)
  deleteTarget.value = null
  activeMenuTaskId.value = ''
  notify(`已删除“${title}”`)
}
function closeMenus(event: MouseEvent) {
  if (!(event.target as HTMLElement).closest('.row-actions')) activeMenuTaskId.value = ''
}
onMounted(() => document.addEventListener('click', closeMenus))
onBeforeUnmount(() => document.removeEventListener('click', closeMenus))
function clearFilters() {
  taskFilter.value = '全部任务'
  appliedTaskFilter.value = '全部任务'
  Object.assign(filters, { videoName: '', transcriptQuery: '', authorQuery: '', collectedFrom: '', collectedTo: '', publishedFrom: '', publishedTo: '', likesMin: '', likesMax: '', favoritesMin: '', favoritesMax: '', sharesMin: '', sharesMax: '' })
  Object.assign(appliedFilters, filters)
}
function applyFilters() {
  appliedTaskFilter.value = taskFilter.value
  Object.assign(appliedFilters, filters)
  notify(`查询完成，找到 ${visibleTasks.value.length} 条视频`)
}
function saveSettings() {
  const persisted = Object.fromEntries(Object.entries(config).filter(([key]) => key !== 'asr.api_key'))
  localStorage.setItem('douyin-workbench-config', JSON.stringify(persisted))
  notify('配置已保存在当前浏览器；API Key 不会被持久化')
}
function resetSettings() {
  localStorage.removeItem('douyin-workbench-config')
  notify('已恢复页面默认值')
}
function valueFor(key: string) { return config[key] }
watch(() => settingsGroup.value, () => { settingsSearch.value = '' })
const savedConfig = localStorage.getItem('douyin-workbench-config')
if (savedConfig) {
  try { Object.assign(config, JSON.parse(savedConfig)) } catch { localStorage.removeItem('douyin-workbench-config') }
}
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand"><div class="brand-mark">采</div><div><strong>采集工作台</strong><span>DOUYIN WORKSPACE</span></div></div>
      <div class="side-label">工作空间</div>
      <button class="nav-item" :class="{ active: page === 'overview' }" @click="page = 'overview'"><span class="nav-icon">▧</span><span>概况预览</span><span v-if="page === 'overview'" class="nav-current"></span></button>
      <button class="nav-item" :class="{ active: page === 'tasks' }" @click="page = 'tasks'"><span class="nav-icon">▦</span><span>任务列表</span><span v-if="page === 'tasks'" class="nav-current"></span></button>
      <div class="side-label settings-label">偏好设置</div>
      <button class="nav-item" :class="{ active: page === 'settings' }" @click="page = 'settings'"><span class="nav-icon">⚙</span><span>采集配置</span><span v-if="page === 'settings'" class="nav-current"></span></button>
      <div class="sidebar-bottom"><div class="runtime-card"><div class="runtime-head"><span class="status-dot"></span>本地工作模式</div><p>当前使用页面模拟数据<br />尚未连接采集服务</p><div class="runtime-chip">前端预览版 <span>v0.1</span></div></div><div class="user-row"><div class="avatar">W</div><div><strong>本机用户</strong><small>个人工作空间</small></div><span class="dots">···</span></div></div>
    </aside>

    <main class="main-area">
      <header class="topbar"><div class="breadcrumb"><span>工作空间</span><b>/</b><strong>{{ page === 'overview' ? '概况预览' : page === 'tasks' ? '任务列表' : '采集配置' }}</strong></div><div class="top-actions"><span class="local-badge"><i></i>本地运行</span><button class="icon-button" aria-label="帮助" @click="notify('页面目前使用模拟数据')">?</button><div class="top-avatar">W</div></div></header>

      <section v-if="page === 'overview'" class="page-content overview-page">
        <div class="page-heading"><div><div class="eyebrow">WORKSPACE <span>·</span> 数据总览</div><h1>概况预览</h1><p>集中查看采集任务与内容处理情况。</p></div><button class="primary-button" @click="page = 'tasks'; showCreate = true"><span>＋</span> 新建任务</button></div>
        <div class="stats-grid"><article class="stat-card"><div class="stat-top"><span>全部任务</span><span class="stat-icon indigo">▦</span></div><strong>{{ tasks.length }}</strong><small>本地任务记录</small></article><article class="stat-card"><div class="stat-top"><span>运行中</span><span class="stat-icon blue">◌</span></div><strong>{{ tasks.filter(t => t.state === '运行中').length }}</strong><small><i class="status-dot"></i>正在处理</small></article><article class="stat-card"><div class="stat-top"><span>已完成</span><span class="stat-icon green">✓</span></div><strong>{{ tasks.filter(t => t.state === '已完成').length }}</strong><small>处理成功</small></article><article class="stat-card"><div class="stat-top"><span>需要关注</span><span class="stat-icon amber">!</span></div><strong>{{ tasks.filter(t => t.state === '失败').length }}</strong><small>查看失败原因</small></article></div>
        <div class="overview-grid"><section class="overview-card"><div class="overview-card-head"><div><h2>最近采集</h2><p>最近添加到工作空间的视频</p></div><button class="text-link" @click="page = 'tasks'">查看全部 →</button></div><div v-for="task in tasks.slice(0, 4)" :key="task.id" class="overview-video"><div class="overview-play">▶</div><div class="overview-video-copy"><strong>{{ task.title }}</strong><small>{{ task.author }} · {{ task.collectedAt }}</small></div><span class="overview-metric">♥ {{ task.likes.toLocaleString() }}</span></div></section><section class="overview-card overview-status"><div class="overview-card-head"><div><h2>处理概况</h2><p>各处理环节的当前状态</p></div></div><div class="overview-status-row"><span class="status-symbol violet">◖</span><div><strong>视频采集</strong><small>采集视频信息与媒体文件</small></div><b>{{ tasks.length }} 条</b></div><div class="overview-status-row"><span class="status-symbol blue">☷</span><div><strong>评论采集</strong><small>评论与回复采集完成</small></div><b>{{ tasks.filter(t => t.commentsState === '已采集').length }} 条</b></div><div class="overview-status-row"><span class="status-symbol green">≋</span><div><strong>语音转写</strong><small>已生成转写内容</small></div><b>{{ tasks.filter(t => t.transcriptState === '已转写').length }} 条</b></div><button class="overview-link" @click="page = 'tasks'">前往任务列表 <span>→</span></button></section></div>
      </section>

      <section v-else-if="page === 'tasks'" class="page-content">
        <div class="page-heading"><div><div class="eyebrow">WORKSPACE <span>·</span> 任务中心</div><h1>任务列表</h1><p>查看最近采集的视频，按内容、时间和互动数据快速筛选。</p></div><button class="primary-button" @click="showCreate = true"><span>＋</span> 新建任务</button></div>

        <section class="search-panel">
          <div class="search-main">
            <label class="search-box title-search"><span>⌕</span><input v-model="filters.videoName" placeholder="视频名称" @keyup.enter="applyFilters" /></label>
            <div class="filter-field inline-date"><label>采集时间</label><div class="range-inputs"><input v-model="filters.collectedFrom" type="date" aria-label="采集时间开始" /><span>至</span><input v-model="filters.collectedTo" type="date" aria-label="采集时间结束" /></div></div>
            <select v-model="taskFilter" aria-label="任务状态"><option>全部任务</option><option>运行中</option><option>已完成</option><option>待处理</option><option>失败</option></select>
            <button class="filter-button" :class="{ active: filtersOpen }" @click="filtersOpen = !filtersOpen">☷ <span>{{ filtersOpen ? '收起筛选' : '更多筛选' }}</span></button>
            <button class="primary-button query-button" @click="applyFilters">查询</button>
          </div>
          <div v-if="filtersOpen" class="advanced-filters">
            <label class="search-box advanced-search"><span>⌕</span><input v-model="filters.transcriptQuery" placeholder="转写内容" @keyup.enter="applyFilters" /></label>
            <label class="search-box advanced-search"><span>⌕</span><input v-model="filters.authorQuery" placeholder="作者名称" @keyup.enter="applyFilters" /></label>
            <div class="filter-field date-filter"><label>视频发布时间</label><div class="range-inputs"><input v-model="filters.publishedFrom" type="date" aria-label="发布时间开始" /><span>至</span><input v-model="filters.publishedTo" type="date" aria-label="发布时间结束" /></div></div>
            <div class="filter-field"><label>点赞数</label><div class="range-inputs"><input v-model="filters.likesMin" type="number" min="0" placeholder="最低" /><span>—</span><input v-model="filters.likesMax" type="number" min="0" placeholder="最高" /></div></div>
            <div class="filter-field"><label>收藏数</label><div class="range-inputs"><input v-model="filters.favoritesMin" type="number" min="0" placeholder="最低" /><span>—</span><input v-model="filters.favoritesMax" type="number" min="0" placeholder="最高" /></div></div>
            <div class="filter-field"><label>转发数</label><div class="range-inputs"><input v-model="filters.sharesMin" type="number" min="0" placeholder="最低" /><span>—</span><input v-model="filters.sharesMax" type="number" min="0" placeholder="最高" /></div></div>
          </div>
          <div class="search-foot"><span>视频名称和采集时间常用条件默认展示，其余筛选可展开</span><div><span>已找到 <b>{{ visibleTasks.length }}</b> 条</span><button class="clear-filters" @click="clearFilters">重置条件</button></div></div>
        </section>

        <section v-if="showCreate" class="modal-backdrop" @click.self="showCreate = false"><div class="create-panel" role="dialog" aria-modal="true" aria-labelledby="create-title"><div class="panel-heading"><div><span class="eyebrow">NEW TASK</span><h2 id="create-title">创建采集任务</h2><p>选择内容来源和处理模块。</p></div><button class="close-button" aria-label="关闭" @click="showCreate = false">×</button></div>
          <div class="form-section"><div class="section-title"><span class="step-number">1</span><div><strong>选择采集来源</strong><small>告诉我们要从哪里获取内容</small></div></div><div class="choice-grid"><button v-for="item in modeOptions" :key="item.id" class="choice-card" :class="{ selected: activeMode === item.id }" @click="activeMode = item.id"><span class="choice-icon">{{ item.icon }}</span><span class="choice-copy"><strong>{{ item.title }}</strong><small>{{ item.desc }}</small></span><span class="radio-dot"></span></button></div>
            <label class="field-label" for="source-url">{{ activeMode === 'video' ? '视频链接' : activeMode === 'collection' ? '合集链接' : '作者主页链接' }}<span>必填</span></label><div class="url-input"><span>↗</span><input id="source-url" v-model="sourceUrl" placeholder="粘贴抖音链接或分享文案" /><button v-if="sourceUrl" class="clear-input" @click="sourceUrl = ''">×</button></div><div class="field-hint">支持粘贴完整分享文案，系统会识别其中的抖音链接。</div>
          </div>
          <div class="form-section"><div class="section-title"><span class="step-number">2</span><div><strong>选择处理模块</strong><small>可同时选择多个模块；转写可在列表中对单条视频单独发起</small></div></div><div class="module-grid"><button v-for="item in moduleOptions" :key="item.id" class="module-card" :class="{ selected: activeModules.includes(item.id) }" @click="toggleModule(item.id)"><span class="module-check">{{ activeModules.includes(item.id) ? '✓' : '' }}</span><strong>{{ item.title }}</strong><small>{{ item.desc }}</small></button></div><div v-if="activeModules.length === 2" class="transcribe-notice"><span>i</span>视频采集与评论采集会在同一任务中依次执行。</div></div>
          <div class="form-section temp-section"><div class="temp-copy"><div class="temp-icon">⚙</div><div><strong>本次任务临时配置</strong><small>默认配置来自“采集配置”页面</small></div></div><button class="switch" :class="{ on: tempConfig }" role="switch" :aria-checked="tempConfig" @click="tempConfig = !tempConfig"><span></span></button><div v-if="tempConfig" class="temp-editor"><p>选择本次要覆盖的配置项，未选择的项目继续使用默认配置。</p><label v-for="option in [{key:'crawl.max_comments_per_video',label:'单视频评论上限'},{key:'inputs.max_videos_per_profile',label:'作者作品上限'},{key:'inputs.max_videos_per_collection',label:'合集作品上限'},{key:'crawl.refresh_completed',label:'重新处理已完成作品'}]" :key="option.key" class="temp-option"><input v-model="selectedSettings" type="checkbox" :value="option.key" /><span>{{ option.label }}</span><b>{{ config[option.key] === '' ? '不限制' : config[option.key] }}</b></label><button class="text-link" @click="page = 'settings'">管理默认配置 <span>↗</span></button></div></div>
          <div class="create-footer"><span><i class="status-dot"></i>任务将在本机队列中处理</span><div><button class="secondary-button" @click="showCreate = false">取消</button><button class="primary-button" @click="createTask">开始任务 <span>→</span></button></div></div>
        </div></section>

        <section class="task-panel"><div class="task-panel-head"><div><h2>最近采集的视频</h2><p>按采集时间倒序排列</p></div><span class="recent-count">{{ visibleTasks.length }} 条记录</span></div>
          <div class="task-table-wrap"><table class="task-table"><thead><tr><th>视频</th><th>作者</th><th>发布时间</th><th>点赞</th><th>收藏</th><th>转发</th><th>采集状态</th><th>操作</th></tr></thead><tbody><tr v-for="task in visibleTasks" :key="task.id"><td><div class="task-name"><div class="task-type-icon" :class="task.mode === '指定作者' ? 'author' : task.mode === '短视频合集' ? 'collection' : ''">{{ task.mode === '指定作者' ? '◎' : task.mode === '短视频合集' ? '▤' : '▶' }}</div><div class="video-title"><strong>{{ task.title }}</strong><small>{{ task.id }} · {{ task.collectedAt }}</small></div><button class="bookmark-button" :class="{ bookmarked: task.inReadingList }" :aria-label="task.inReadingList ? '从阅读列表移除' : '加入阅读'" :title="task.inReadingList ? '已加入阅读' : '加入阅读'" @click.stop="toggleReading(task)"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.75 4.75h10.5a1 1 0 0 1 1 1v14l-6.25-3.8-6.25 3.8v-14a1 1 0 0 1 1-1Z" /></svg></button></div></td><td><div class="author-cell">{{ task.author }}</div></td><td class="time-cell">{{ task.publishedAt || '—' }}</td><td class="metric-cell">{{ task.likes.toLocaleString() }}</td><td class="metric-cell">{{ task.favorites.toLocaleString() }}</td><td class="metric-cell">{{ task.shares.toLocaleString() }}</td><td><div class="step-statuses"><div v-for="step in [{ key: 'video', label: '视频已下载', status: task.videoStatus }, { key: 'comments', label: '评论已扫描', status: task.commentsStatus }, { key: 'transcript', label: '转写已完成', status: task.transcriptStatus }]" :key="step.key" class="step-status" :class="step.status" :title="statusLabel(step.label, step.status)" :aria-label="statusLabel(step.label, step.status)"><span class="step-indicator">{{ step.status === 'done' ? '✓' : step.status === 'failed' ? '×' : step.status === 'running' ? '…' : '·' }}</span><small>{{ step.key === 'video' ? '视频' : step.key === 'comments' ? '评论' : '转写' }}</small></div></div></td><td><div class="row-actions"><button class="more-button" :aria-label="activeMenuTaskId === task.id ? '收起操作菜单' : '打开操作菜单'" :aria-expanded="activeMenuTaskId === task.id" @click.stop="activeMenuTaskId = activeMenuTaskId === task.id ? '' : task.id"><span>⋮</span> 操作 <b>{{ activeMenuTaskId === task.id ? '⌃' : '⌄' }}</b></button><div v-if="activeMenuTaskId === task.id" class="action-menu"><button :disabled="task.transcriptState === '转写中'" @click.stop="transcribe(task)"><span>◖</span>{{ task.transcriptState === '已转写' ? '重新转写' : task.transcriptState === '转写中' ? '转写中…' : '转写' }}<small>{{ task.transcriptState === '已转写' ? '覆盖现有转写内容' : task.transcriptState }}</small></button><button :disabled="task.commentsState === '采集中'" @click.stop="pullComments(task)"><span>☷</span>{{ task.commentsState === '已采集' ? '重新拉取评论' : task.commentsState === '采集中' ? '正在拉取评论…' : '拉取评论' }}<small>{{ task.commentsState === '已采集' ? '更新评论及回复' : task.commentsState }}</small></button><div class="menu-divider"></div><button class="danger-action" @click.stop="deleteTarget = task; activeMenuTaskId = ''"><span>⌫</span>删除<small>移除视频及关联数据</small></button></div></div></td></tr><tr v-if="visibleTasks.length === 0"><td colspan="8" class="empty-state">没有符合条件的视频，试试调整筛选条件</td></tr></tbody></table></div><div class="table-footer"><span>显示最近 <b>{{ visibleTasks.length }}</b> 条视频</span><div class="pagination"><button disabled>‹</button><button class="current-page">1</button><button disabled>›</button></div></div>
        </section>
        <div class="demo-note"><span>i</span><div><strong>当前为前端预览</strong><p>任务和配置保存在当前浏览器中，尚未连接 Python 采集服务。</p></div><button @click="page = 'settings'">前往配置 <span>→</span></button></div>
      </section>

      <section v-else class="page-content settings-page">
        <div class="page-heading"><div><div class="eyebrow">PREFERENCES <span>·</span> 配置管理</div><h1>采集配置</h1><p>按功能管理所有运行参数，新建任务时可作为默认配置使用。</p></div><div class="heading-actions"><button class="secondary-button" @click="resetSettings">恢复默认</button><button class="primary-button" @click="saveSettings">保存配置 <span>✓</span></button></div></div>
        <div class="settings-layout"><aside class="settings-nav"><div class="settings-nav-title">配置分区 <span>{{ groups.length }}</span></div><button v-for="group in groups" :key="group.id" class="settings-nav-item" :class="{ selected: settingsGroup === group.id }" @click="settingsGroup = group.id"><span class="group-icon">{{ group.icon }}</span><span class="group-copy"><strong>{{ group.label }}</strong><small>{{ group.caption }}</small></span><span v-if="settingsGroup === group.id" class="nav-current"></span></button><div class="coverage-note"><span>✓</span><div><strong>配置项已覆盖</strong><small>{{ settingCount }} 项 · 与示例配置对应</small></div></div></aside>
          <div class="settings-content"><div class="settings-content-head"><div><div class="eyebrow">CONFIGURATION</div><h2>{{ activeGroup.label }}</h2><p>{{ activeGroup.caption }}<span class="config-count">{{ visibleSettings.length }} 项配置</span></p></div><label class="search-box settings-search"><span>⌕</span><input v-model="settingsSearch" placeholder="搜索当前分区" /></label></div><div class="settings-card"><div v-for="item in visibleSettings" :key="item.key" class="setting-row" :class="{ 'textarea-row': item.kind === 'textarea' }"><div class="setting-info"><label :for="item.key">{{ item.label }}</label><small>{{ item.description }}</small></div><div class="setting-control" :class="{ 'toggle-control': item.kind === 'toggle' }"><textarea v-if="item.kind === 'textarea'" :id="item.key" v-model="config[item.key]" rows="3" placeholder="每行一个链接，可留空"></textarea><div v-else-if="item.kind === 'secret'" class="secret-input"><input :id="item.key" v-model="config[item.key]" :type="revealKey ? 'text' : 'password'" placeholder="填写 MiMo API Key" /><button @click="revealKey = !revealKey">{{ revealKey ? '隐藏' : '显示' }}</button></div><select v-else-if="item.kind === 'select'" :id="item.key" v-model="config[item.key]"><option v-for="option in item.options" :key="option" :value="option">{{ option }}</option></select><button v-else-if="item.kind === 'toggle'" :id="item.key" class="switch" :class="{ on: config[item.key] }" role="switch" :aria-checked="Boolean(config[item.key])" @click="config[item.key] = !config[item.key]"><span></span></button><div v-else class="value-input"><input :id="item.key" v-model="config[item.key]" :type="item.kind === 'number' ? 'number' : 'text'" :placeholder="item.kind === 'number' ? '不限制' : ''" /><span v-if="item.unit">{{ item.unit }}</span></div><code>{{ item.key }}</code></div></div><div v-if="visibleSettings.length === 0" class="empty-settings">没有找到匹配的配置项</div></div><div class="settings-tip"><span>i</span><p>修改在点击“保存配置”后保存在当前浏览器。连接后端后，这里将读写本机的 config.yaml。</p></div></div>
        </div>
      </section>
    </main>
    <div v-if="deleteTarget" class="modal-backdrop confirm-backdrop" @click.self="deleteTarget = null"><section class="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="delete-title"><div class="confirm-icon">!</div><h2 id="delete-title">删除这条视频？</h2><p>“{{ deleteTarget.title }}”及其关联的评论、转写等数据将从列表中移除。此操作无法撤销。</p><div class="confirm-actions"><button class="secondary-button" @click="deleteTarget = null">取消</button><button class="danger-button" @click="confirmDelete">确认删除</button></div></section></div>
    <Transition name="toast"><div v-if="toast" class="toast-message"><span>✓</span>{{ toast }}</div></Transition>
  </div>
</template>
