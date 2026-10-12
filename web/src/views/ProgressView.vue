<template>
  <div>
    <div class="card">
      <h2>学习进度</h2>
      <div class="field">
        <label>课程</label>
        <select v-model.number="courseId" @change="load">
          <option :value="0" disabled>请选择课程</option>
          <option v-for="c in courses" :key="c.course_id" :value="c.course_id">{{ c.name }}</option>
        </select>
        <span class="tip">总体掌握度 {{ average }}%（{{ doneCount }} / {{ tasks.length }} 项任务已完成）</span>
      </div>

      <div v-if="points.length === 0" class="empty">该课程还没有知识点数据。</div>
      <div v-else class="bars">
        <div v-for="p in points" :key="p.point_id" class="bar-row">
          <span class="bar-name">{{ p.name }}</span>
          <span class="bar-track"><span class="bar-fill" :style="{ width: p.mastery + '%' }"></span></span>
          <span class="bar-value">{{ p.mastery }}%</span>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>每日任务</h2>
      <div v-if="tasks.length === 0" class="empty">还没有任务，先到“学习计划”页面生成并确认计划。</div>
      <table v-else>
        <thead>
          <tr><th>日期</th><th>任务</th><th>时长</th><th>状态</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="t in tasks" :key="t.task_id">
            <td>{{ t.task_date }}</td>
            <td>{{ t.title }}</td>
            <td>{{ t.minutes }} 分钟</td>
            <td>
              <span :class="t.status === 'done' ? 'badge done' : 'badge'">
                {{ t.status === 'done' ? '已完成' : '待完成' }}
              </span>
            </td>
            <td>
              <button v-if="t.status !== 'done'" class="ghost" @click="openForm(t)">填写完成情况</button>
              <span v-else class="tip">{{ t.note || '—' }}</span>
            </td>
          </tr>
        </tbody>
      </table>

      <div v-if="active" class="complete-form">
        <p>
          任务：<b>{{ active.title }}</b>
          <span class="tip">（当前掌握度 {{ suggestion.current_mastery }}%，系统建议 {{ suggestion.suggested_mastery }}%）</span>
        </p>
        <div class="field">
          <label>确认掌握度</label>
          <input v-model.number="form.mastery" type="number" min="0" max="100" class="short" />
          <label class="inline">完成备注</label>
          <input v-model="form.note" maxlength="100" placeholder="例如：完成课后习题" />
          <button :disabled="submitting" @click="submitComplete">{{ submitting ? '提交中…' : '提交并更新掌握度' }}</button>
        </div>
      </div>

      <div class="field" style="margin-top: 12px">
        <label></label>
        <button class="ghost" :disabled="!courseId" @click="adjust">调整后续计划</button>
        <span v-if="message" :class="ok ? 'tip' : 'error'">{{ message }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { adjustPlan, completeTask, listCourses, listPoints, listTasks, taskSuggestion } from '../api'

const route = useRoute()
const courses = ref([])
const courseId = ref(0)
const points = ref([])
const tasks = ref([])
const active = ref(null)
const suggestion = ref({ current_mastery: 0, suggested_mastery: 0 })
const message = ref('')
const ok = ref(true)
const submitting = ref(false)
const form = reactive({ mastery: 0, note: '' })

const average = computed(() =>
  points.value.length
    ? Math.round(points.value.reduce((sum, p) => sum + p.mastery, 0) / points.value.length)
    : 0
)
const doneCount = computed(() => tasks.value.filter((t) => t.status === 'done').length)

async function loadCourses() {
  const data = await listCourses()
  courses.value = data.items
  const query = Number(route.query.course || 0)
  courseId.value = query || (courses.value.length ? courses.value[0].course_id : 0)
  if (courseId.value) await load()
}

async function load() {
  active.value = null
  const [pointData, taskData] = await Promise.all([listPoints(courseId.value), listTasks(courseId.value)])
  points.value = pointData.items
  tasks.value = taskData.items
}

async function openForm(task) {
  active.value = task
  message.value = ''
  try {
    suggestion.value = await taskSuggestion(task.task_id)
    form.mastery = suggestion.value.suggested_mastery
    form.note = ''
  } catch (e) {
    message.value = e.message
    ok.value = false
  }
}

async function submitComplete() {
  submitting.value = true
  const pointName = suggestion.value.point_name
  try {
    const data = await completeTask(active.value.task_id, { mastery: form.mastery, note: form.note })
    await load()
    ok.value = true
    message.value = `任务已完成，${pointName} 掌握度 ${data.old_mastery}% → ${data.new_mastery}%`
  } catch (e) {
    ok.value = false
    message.value = e.message
  } finally {
    submitting.value = false
  }
}

async function adjust() {
  message.value = ''
  try {
    const data = await adjustPlan(courseId.value)
    ok.value = true
    message.value = `已根据考试日期（${data.exam_date}）重新排期 ${data.moved} 个未完成任务`
    await load()
  } catch (e) {
    ok.value = false
    message.value = e.message
  }
}

onMounted(loadCourses)
</script>
