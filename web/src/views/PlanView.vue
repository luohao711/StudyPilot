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
        <button :disabled="!courseId || generating" @click="generate">
          {{ generating ? '生成中…' : '生成候选计划' }}
        </button>
        <button class="ghost" :disabled="!courseId" @click="addManualTask">手工添加任务</button>
        <span v-if="notice" class="tip">{{ notice }}</span>
      </div>
      <div v-if="error" class="error">{{ error }}</div>

      <div v-if="tasks.length === 0" class="empty">还没有候选任务，点击“生成候选计划”。</div>
      <table v-else>
        <thead>
          <tr><th>日期</th><th>任务</th><th>时长(分钟)</th><th>说明</th></tr>
        </thead>
        <tbody>
          <tr v-for="(t, i) in tasks" :key="i">
            <td><input v-model="t.date" class="short" /></td>
            <td><input v-model="t.title" /></td>
            <td><input v-model.number="t.minutes" type="number" class="short" /></td>
            <td class="tip">{{ t.reason || '手工添加' }}</td>
          </tr>
        </tbody>
      </table>

      <div class="field" style="margin-top: 12px">
        <label></label>
        <button :disabled="tasks.length === 0 || confirming" @click="confirm">
          {{ confirming ? '保存中…' : '确认计划' }}
        </button>
        <span v-if="message" :class="ok ? 'tip' : 'error'">{{ message }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { addPoint, confirmPlan, generatePlan, getGoal, listCourses, listPoints } from '../api'

const route = useRoute()
const courses = ref([])
const courseId = ref(0)
const points = ref([])
const goal = ref(null)
const tasks = ref([])
const generating = ref(false)
const confirming = ref(false)
const adding = ref(false)
const error = ref('')
const notice = ref('')
const message = ref('')
const ok = ref(true)
const pointForm = reactive({ name: '', mastery: 0 })

const courseName = computed(() => courses.value.find((c) => c.course_id === courseId.value)?.name || '')

async function loadCourses() {
  const data = await listCourses()
  courses.value = data.items
  const query = Number(route.query.course || 0)
  if (query) courseId.value = query
  else if (courses.value.length) courseId.value = courses.value[0].course_id
  if (courseId.value) await onCourseChange()
}

async function onCourseChange() {
  error.value = ''
  message.value = ''
  tasks.value = []
  notice.value = ''
  const [goalData, pointData] = await Promise.all([getGoal(courseId.value), listPoints(courseId.value)])
  goal.value = goalData.goal
  points.value = pointData.items
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

async function generate() {
  generating.value = true
  error.value = ''
  message.value = ''
  try {
    const data = await generatePlan({ course_id: courseId.value })
    tasks.value = data.tasks
    notice.value = data.ai_available
      ? '已由模型服务生成候选计划'
      : '模型服务未配置，已使用规则模板生成候选计划，确认前可以修改'
  } catch (e) {
    tasks.value = []
    notice.value = ''
    error.value = `${e.message}（可点击“手工添加任务”继续）`
  } finally {
    generating.value = false
  }
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
  notice.value = '已切换为手工排期'
}

async function confirm() {
  confirming.value = true
  message.value = ''
  try {
    const data = await confirmPlan({ course_id: courseId.value, tasks: tasks.value })
    ok.value = true
    message.value = `计划已保存，共 ${data.task_count} 个任务`
  } catch (e) {
    ok.value = false
    message.value = e.message
  } finally {
    confirming.value = false
  }
}

onMounted(loadCourses)
</script>
