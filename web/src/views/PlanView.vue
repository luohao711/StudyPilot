<template>
  <div>
    <div class="card">
      <h2>知识点掌握度</h2>
      <div class="field">
        <label>课程</label>
        <select v-model.number="courseId" @change="onCourseChange">
          <option :value="0" disabled>请选择课程</option>
          <option v-for="c in courses" :key="c.course_id" :value="c.course_id">{{ c.name }}</option>
        </select>
        <span v-if="goal" class="tip">考试：{{ goal.exam_date }}，每天可用 {{ goal.available_minutes }} 分钟</span>
        <span v-else class="tip">尚未设置考试目标</span>
      </div>

      <div v-if="courseId && points.length === 0" class="empty">
        还没有知识点，先添加知识点并填写当前掌握度。
      </div>
      <table v-else-if="points.length">
        <thead>
          <tr><th>知识点</th><th>掌握度</th><th>更新时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="p in points" :key="p.point_id">
            <td>{{ p.name }}</td>
            <td>{{ p.mastery }}%</td>
            <td class="tip">{{ p.updated_at }}</td>
          </tr>
        </tbody>
      </table>

      <div class="field" style="margin-top: 12px">
        <label>新增知识点</label>
        <input v-model="pointForm.name" maxlength="20" placeholder="例如：软件测试" />
        <input v-model.number="pointForm.mastery" type="number" min="0" max="100" class="short" />
        <span class="tip">分</span>
        <button class="ghost" :disabled="!courseId || adding" @click="addPointItem">
          {{ adding ? '添加中…' : '添加' }}
        </button>
      </div>
    </div>

    <div class="card">
      <h2>生成学习计划</h2>
      <div class="field">
        <label></label>
        <button :disabled="!courseId || generating || jobActive" @click="generate">
          {{ generating ? '生成中…' : '生成候选计划（规则）' }}
        </button>
        <button :disabled="!courseId || jobActive || aiGenerating" @click="generateAI">
          {{ jobActive ? 'AI 生成中…' : 'AI 生成计划（后台任务）' }}
        </button>
        <button class="ghost" :disabled="!courseId || jobActive" @click="addManualTask">手工添加任务</button>
        <span v-if="notice" class="tip">{{ notice }}</span>
      </div>
      <div v-if="error" class="error">{{ error }}</div>

      <div v-if="job" class="job-box" :class="job.status">
        <p class="job-head">
          <b>AI 任务</b>　{{ job.job_id }}　<span class="badge" :class="jobBadgeClass">{{ job.status_label }}</span>
          <span v-if="jobActive" class="tip">　{{ waitText }}（模型调用通常 4—8 秒，超时 45 秒）</span>
          <span v-else-if="job.finished_at" class="tip">　结束于 {{ job.finished_at }}</span>
        </p>
        <p v-if="jobActive" class="tip">已提交到后台，可以离开本页或刷新，回来后任务状态会恢复；无需停留在当前页面。</p>
        <p v-if="job.deduplicated" class="tip">检测到同一门课程已有进行中的任务，已直接接入该任务，没有重复调用模型。</p>
        <p v-if="job.status === 'queued'" class="tip">排队中：前面的生成任务完成后自动开始。</p>
        <p v-if="job.status === 'canceled'" class="tip">任务已取消，迟到的模型结果不会写入。你可以重新生成、重试或手工排期。</p>
        <p v-if="job.status === 'failed'" class="error">
          失败原因：{{ job.error && job.error.code }}　{{ job.error && job.error.message }}
        </p>
        <p v-if="job.status === 'failed'" class="tip">{{ errorHint }}</p>
        <div class="actions" v-if="jobActive || job.status === 'failed' || job.status === 'canceled'">
          <button v-if="jobActive" class="ghost" @click="cancelJob">取消任务</button>
          <button v-if="job.status === 'failed' || job.status === 'canceled'" @click="retryJob">重试</button>
          <button class="ghost" @click="abandonJob">放弃结果</button>
          <span class="tip">重试会创建新的任务编号；取消后不会重复保存结果。</span>
        </div>
      </div>

      <div v-if="aiResult" class="ai-box">
        <p>
          <b>AI 计划</b>　{{ aiResult.generator }} / Prompt {{ aiResult.prompt_version }}　
          <span :class="aiResult.checks && aiResult.checks.passed ? 'badge done' : 'badge'">
            程序校验{{ aiResult.checks && aiResult.checks.passed ? '通过' : '未通过' }}
          </span>　
          修复 {{ aiResult.meta.repair_attempts || 0 }} 次　模型调用 {{ aiResult.meta.calls }} 次　
          耗时 {{ aiResult.meta.latency_ms }} ms　tokens {{ aiResult.meta.total_tokens }}　
          费用约 {{ aiResult.meta.cost_yuan }} 元
        </p>
        <p v-if="aiResult.status === 'fallback'" class="tip">
          降级原因：{{ aiResult.fallback_reason }}（模型输出或服务不可用，已自动改用规则模板，AI 结果不完整）
        </p>
        <p v-if="aiResult.checks && aiResult.checks.issues && aiResult.checks.issues.length" class="tip">
          校验问题：{{ aiResult.checks.issues.join('、') }}
        </p>
        <p class="tip">{{ aiResult.summary }}</p>
        <p v-if="aiResult.notes" class="tip">取舍说明：{{ aiResult.notes }}</p>
        <p v-if="aiResult.status === 'fallback'" class="warn">
          以上为规则模板降级结果（不是模型生成），任务描述较笼统；可手工修改后保存，稍后重试 AI。
        </p>
        <p v-else class="warn">
          以下内容由模型生成，可能存在内容或排期错误。程序只校验日期、每天时长和知识点覆盖，
          任务是否具体、理由是否合理需要你逐条检查；可以直接修改、重新生成、改用规则计划或手工排期。
        </p>
      </div>
      <p v-if="job && job.confirmed_plan_id" class="tip">
        该任务结果已保存为计划 #{{ job.confirmed_plan_id }}，重复点击不会重复写入。
      </p>

      <div v-if="tasks.length === 0" class="empty">还没有候选任务，点击上方按钮生成或手工添加。</div>
      <table v-else>
        <thead>
          <tr><th>日期</th><th>任务</th><th>时长(分钟)</th><th>说明</th></tr>
        </thead>
        <tbody>
          <tr v-for="(t, i) in tasks" :key="i">
            <td><input v-model="t.date" class="short" /></td>
            <td><input v-model="t.title" maxlength="50" class="title-input" /></td>
            <td><input v-model.number="t.minutes" type="number" class="short" min="10" max="300" /></td>
            <td class="tip">{{ t.reason || '手工添加' }}</td>
          </tr>
        </tbody>
      </table>

      <div class="field" style="margin-top: 12px">
        <label></label>
        <button :disabled="tasks.length === 0 || confirming || (job && job.confirmed_plan_id)" @click="confirm">
          {{ confirming ? '保存中…' : '保存为正式计划' }}
        </button>
        <button v-if="aiResult" class="ghost" :disabled="jobActive" @click="regenerate">重新生成</button>
        <span v-if="message" :class="ok ? 'tip' : 'error'">{{ message }}</span>
      </div>

      <div v-if="jobs.length" style="margin-top: 14px">
        <h3 class="sub">最近 AI 任务（刷新或重连后可从这里恢复）</h3>
        <table>
          <thead>
            <tr><th>任务编号</th><th>状态</th><th>创建时间</th><th>结束时间</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in jobs" :key="item.job_id">
              <td class="mono">{{ item.job_id }}</td>
              <td><span class="badge" :class="badgeClass(item)">{{ item.status_label }}</span></td>
              <td class="tip">{{ item.created_at }}</td>
              <td class="tip">{{ item.finished_at || '—' }}</td>
              <td>
                <button class="link" :disabled="jobActive && (!job || item.job_id !== job.job_id)" @click="viewJob(item)">查看</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  addPoint,
  cancelPlanJob,
  confirmPlan,
  confirmPlanJob,
  createPlanJob,
  generatePlan,
  getPlanJob,
  getGoal,
  listCourses,
  listPlanJobs,
  listPoints,
  retryPlanJob
} from '../api'

const route = useRoute()
const courses = ref([])
const courseId = ref(0)
const points = ref([])
const goal = ref(null)
const tasks = ref([])
const generating = ref(false)
const aiGenerating = ref(false)
const confirming = ref(false)
const adding = ref(false)
const error = ref('')
const notice = ref('')
const message = ref('')
const ok = ref(true)
const pointForm = reactive({ name: '', mastery: 0 })
const job = ref(null)
const jobs = ref([])
const elapsed = ref(0)
let pollTimer = null
let tickTimer = null

const jobActive = computed(() => job.value && ['queued', 'running'].includes(job.value.status))
const aiResult = computed(() => (job.value && job.value.status === 'succeeded' ? job.value.result : null))
const jobBadgeClass = computed(() => (job.value && job.value.status === 'succeeded' ? 'done' : job.value && job.value.status === 'failed' ? 'bad' : ''))
const waitText = computed(() => {
  const seconds = elapsed.value
  if (!job.value) return ''
  return job.value.status === 'queued' ? `已排队 ${seconds} 秒` : `已等待 ${seconds} 秒，无需刷新`
})
const errorHint = computed(() => {
  const code = job.value && job.value.error ? job.value.error.code : ''
  if (code === 'LLM_TIMEOUT') return '模型服务超时：可以重试；如果多次超时，可改用规则计划或手工排期。'
  if (code === 'LLM_CONNECTION_ERROR' || code === 'LLM_NOT_CONFIGURED' || code === 'LLM_AUTH_FAILED') {
    return 'AI 服务暂不可用：可以重试，或改用规则计划继续完成排期，不影响已保存的任务。'
  }
  if (code === 'JOB_INTERRUPTED') return '服务重启导致任务中断：请重新生成或重试。'
  if (code === 'DATABASE_UNAVAILABLE') return '数据库暂时不可用：请稍后重试，已保存的计划不受影响。'
  return '可以重试；如果持续失败，改用规则计划或手工添加任务，后续再试 AI。'
})

function badgeClass(item) {
  if (item.status === 'succeeded') return 'done'
  if (item.status === 'failed') return 'bad'
  return ''
}

function clearTimers() {
  if (pollTimer) clearTimeout(pollTimer)
  if (tickTimer) clearInterval(tickTimer)
  pollTimer = null
  tickTimer = null
}

function elapsedFrom(item) {
  if (!item) return 0
  const start = new Date((item.started_at || item.created_at || '').replace(' ', 'T'))
  if (Number.isNaN(start.getTime())) return 0
  const end = item.finished_at ? new Date(item.finished_at.replace(' ', 'T')) : new Date()
  return Math.max(0, Math.round((end - start) / 1000))
}

function startTicking() {
  if (tickTimer) clearInterval(tickTimer)
  elapsed.value = elapsedFrom(job.value)
  tickTimer = setInterval(() => {
    elapsed.value = elapsedFrom(job.value)
  }, 1000)
}

function adopt(jobData, { restoreResult = true } = {}) {
  job.value = jobData
  error.value = ''
  if (jobData.deduplicated) notice.value = '已接入该课程正在执行的任务'
  if (['queued', 'running'].includes(jobData.status)) {
    startTicking()
    schedulePoll()
    return
  }
  clearTimers()
  elapsed.value = elapsedFrom(jobData)
  if (jobData.status === 'succeeded' && restoreResult && jobData.result) {
    tasks.value = (jobData.result.tasks || []).map((t) => ({ ...t }))
    source.value = 'ai'
    notice.value = jobData.result.status === 'fallback'
      ? '模型未完成本次生成，已降级为规则模板，可手工调整后保存'
      : 'AI 已生成候选计划并经过程序约束检查，请逐条检查后保存'
    message.value = ''
  }
  if (jobData.status === 'canceled') notice.value = '任务已取消，未保存任何结果'
}

function schedulePoll() {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = setTimeout(poll, 1200)
}

async function poll() {
  if (!job.value || !jobActive.value) return
  try {
    const data = await getPlanJob(job.value.job_id)
    const wasActive = jobActive.value
    job.value = data
    if (wasActive && !jobActive.value) {
      clearTimers()
      elapsed.value = elapsedFrom(data)
      if (data.status === 'succeeded' && data.result) {
        tasks.value = (data.result.tasks || []).map((t) => ({ ...t }))
        source.value = 'ai'
        notice.value = data.result.status === 'fallback'
          ? '模型未完成本次生成，已降级为规则模板，可手工调整后保存'
          : 'AI 已生成候选计划，请逐条检查后保存'
      }
      if (data.status === 'failed') notice.value = ''
      if (data.status === 'canceled') notice.value = '任务已取消，未保存任何结果'
      await refreshJobList()
      return
    }
    schedulePoll()
  } catch (e) {
    // 网络短暂中断时继续轮询，页面保留当前状态；连续失败由用户手动刷新恢复。
    error.value = `查询任务状态失败：${e.message}（稍后自动重试）`
    schedulePoll()
  }
}

async function refreshJobList() {
  if (!courseId.value) return
  try {
    jobs.value = (await listPlanJobs(courseId.value, 5)).items
  } catch (e) {
    // 历史列表失败不影响主流程
  }
}

async function restoreJobs() {
  if (!courseId.value) return
  try {
    const data = await listPlanJobs(courseId.value, 5)
    jobs.value = data.items
    const active = data.items.find((item) => ['queued', 'running'].includes(item.status))
    if (active) {
      adopt(active)
      return
    }
    const latest = data.items[0]
    if (latest && latest.status === 'succeeded' && latest.result) {
      adopt(latest)
      if (latest.confirmed_plan_id) {
        notice.value = `已恢复最近一次任务结果；该结果已保存为计划 #${latest.confirmed_plan_id}`
      }
    } else if (latest && latest.status === 'failed') {
      job.value = latest
      elapsed.value = elapsedFrom(latest)
      notice.value = '最近一次 AI 任务失败，可重试或改用规则计划'
    } else if (latest && latest.status === 'canceled') {
      job.value = latest
      notice.value = '最近一次 AI 任务已取消'
    }
  } catch (e) {
    // 恢复失败不阻塞页面
  }
}

async function loadCourses() {
  const data = await listCourses()
  courses.value = data.items
  const query = Number(route.query.course || 0)
  if (query) courseId.value = query
  else if (courses.value.length) courseId.value = courses.value[0].course_id
  if (courseId.value) await onCourseChange()
}

async function onCourseChange() {
  clearTimers()
  error.value = ''
  message.value = ''
  notice.value = ''
  tasks.value = []
  job.value = null
  jobs.value = []
  source.value = ''
  const [goalData, pointData] = await Promise.all([getGoal(courseId.value), listPoints(courseId.value)])
  goal.value = goalData.goal
  points.value = pointData.items
  await restoreJobs()
}

async function addPointItem() {
  adding.value = true
  error.value = ''
  try {
    await addPoint(courseId.value, { name: pointForm.name, mastery: pointForm.mastery })
    pointForm.name = ''
    pointForm.mastery = 0
    points.value = (await listPoints(courseId.value)).items
  } catch (e) {
    error.value = e.message
  } finally {
    adding.value = false
  }
}

const source = ref('')

function newKey() {
  const bytes = new Uint8Array(8)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

async function startJob(payload) {
  aiGenerating.value = true
  error.value = ''
  message.value = ''
  notice.value = ''
  tasks.value = []
  source.value = ''
  try {
    const data = await createPlanJob({
      course_id: courseId.value,
      idempotency_key: newKey(),
      ...payload
    })
    adopt(data, { restoreResult: false })
    notice.value = data.deduplicated ? '已有进行中的任务，已接入' : 'AI 任务已提交，页面会自动更新状态，也可以先离开本页'
    await refreshJobList()
  } catch (e) {
    error.value = `AI 生成失败：${e.message}（可改用规则计划或手工排期）`
    aiGenerating.value = false
  }
}

async function generateAI() {
  const payload = {}
  const sim = String(route.query.sim || '')
  if (sim) payload.simulate = sim
  await startJob(payload)
  aiGenerating.value = false
}

async function generate() {
  generating.value = true
  error.value = ''
  message.value = ''
  try {
    const data = await generatePlan({ course_id: courseId.value })
    tasks.value = data.tasks
    source.value = 'rule'
    notice.value = data.ai_available
      ? '已由模型服务生成候选计划'
      : '模型服务未配置，已使用规则模板生成候选计划，保存前可以修改'
  } catch (e) {
    tasks.value = []
    notice.value = ''
    error.value = `${e.message}（可点击“手工添加任务”继续）`
  } finally {
    generating.value = false
  }
}

async function cancelJob() {
  if (!job.value) return
  try {
    adopt(await cancelPlanJob(job.value.job_id), { restoreResult: false })
    await refreshJobList()
  } catch (e) {
    error.value = `取消失败：${e.message}`
  }
}

async function retryJob() {
  if (!job.value) return
  aiGenerating.value = true
  error.value = ''
  try {
    const data = await retryPlanJob(job.value.job_id)
    tasks.value = []
    adopt(data, { restoreResult: false })
    notice.value = '重试任务已提交，正在后台重新生成'
    await refreshJobList()
  } catch (e) {
    error.value = `重试失败：${e.message}`
  } finally {
    aiGenerating.value = false
  }
}

function abandonJob() {
  clearTimers()
  job.value = null
  tasks.value = []
  notice.value = '已放弃这条 AI 结果，可重新生成或手工排期'
  error.value = ''
  source.value = ''
}

async function regenerate() {
  await startJob({})
}

function addManualTask() {
  const day = new Date()
  day.setDate(day.getDate() + tasks.value.length + 1)
  tasks.value.push({
    point_id: null,
    title: '',
    date: day.toISOString().slice(0, 10),
    minutes: 30,
    reason: ''
  })
  source.value = source.value || 'manual'
  notice.value = '已切换为手工排期'
}

async function confirm() {
  confirming.value = true
  message.value = ''
  try {
    let data
    if (job.value && job.value.status === 'succeeded' && source.value === 'ai') {
      data = await confirmPlanJob(job.value.job_id, tasks.value)
    } else {
      data = await confirmPlan({ course_id: courseId.value, tasks: tasks.value })
    }
    ok.value = true
    message.value = data.duplicate
      ? `该任务已保存过，未重复写入（计划 #${data.plan_id}）`
      : `计划已保存，共 ${data.task_count} 个任务（计划 #${data.plan_id}）`
    if (job.value && data.job_id) {
      const refreshed = await getPlanJob(job.value.job_id)
      job.value = refreshed
    }
    await refreshJobList()
  } catch (e) {
    ok.value = false
    message.value = e.message
  } finally {
    confirming.value = false
  }
}

function viewJob(item) {
  if (item.confirmed_plan_id && item.status === 'succeeded') {
    notice.value = `任务 ${item.job_id} 已保存为计划 #${item.confirmed_plan_id}`
  }
  adopt(item)
  if (item.status === 'failed') error.value = ''
}

onMounted(loadCourses)
onBeforeUnmount(clearTimers)

// 从首页课程卡片或外部链接切换到另一门课程时，路由复用同一个页面组件，
// 需要监听 query 变化并重新加载课程数据，否则会停留在上一门课程。
watch(
  () => route.query.course,
  (value) => {
    const id = Number(value || 0)
    if (id && id !== courseId.value) {
      courseId.value = id
      onCourseChange()
    }
  }
)
</script>
