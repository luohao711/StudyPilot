<template>
  <div>
    <div class="card">
      <h2>我的课程</h2>
      <div v-if="loading" class="tip">正在加载课程…</div>
      <div v-else-if="loadError" class="error">{{ loadError }}</div>
      <div v-else-if="courses.length === 0" class="empty">
        还没有课程，先在下面创建一门课程。
      </div>
      <div v-else class="grid">
        <div v-for="c in courses" :key="c.course_id" class="course">
          <div class="course-head">
            <strong>{{ c.name }}</strong>
            <span class="tip">{{ c.term || '—' }}</span>
          </div>
          <p class="tip">
            考试：{{ c.exam_date || '未设置' }}
            <span v-if="c.target_score">／目标 {{ c.target_score }} 分</span>
          </p>
          <p>
            掌握度：<b>{{ c.avg_mastery === null ? '—' : c.avg_mastery + '%' }}</b>
            <span class="tip">（{{ c.point_count }} 个知识点，待完成 {{ c.todo_count }} 项）</span>
          </p>
          <div class="actions">
            <button class="ghost" @click="pickGoal(c)">设置考试目标</button>
            <router-link class="link" :to="{ path: '/plan', query: { course: c.course_id } }">学习计划</router-link>
            <router-link class="link" :to="{ path: '/progress', query: { course: c.course_id } }">学习进度</router-link>
          </div>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>设置考试目标</h2>
      <div class="field">
        <label>课程</label>
        <select v-model.number="goal.course_id">
          <option :value="0" disabled>请选择课程</option>
          <option v-for="c in courses" :key="c.course_id" :value="c.course_id">{{ c.name }}</option>
        </select>
      </div>
      <div class="field">
        <label>考试日期</label>
        <input v-model="goal.exam_date" placeholder="YYYY-MM-DD" />
        <label class="inline">目标分数</label>
        <input v-model.number="goal.target_score" type="number" min="1" max="100" class="short" />
        <label class="inline">每天可用(分钟)</label>
        <input v-model.number="goal.available_minutes" type="number" min="30" max="600" class="short" />
      </div>
      <div class="field">
        <label></label>
        <button :disabled="saving" @click="submitGoal">{{ saving ? '保存中…' : '保存目标' }}</button>
        <span v-if="goalMessage" :class="goalOk ? 'tip' : 'error'">{{ goalMessage }}</span>
      </div>
    </div>

    <div class="card">
      <h2>创建课程</h2>
      <div class="field">
        <label>课程名称</label>
        <input v-model="form.name" maxlength="30" placeholder="例如：软件工程" />
      </div>
      <div class="field">
        <label>学期</label>
        <input v-model="form.term" maxlength="20" placeholder="例如：2026春" />
      </div>
      <div class="field">
        <label></label>
        <button :disabled="submitting" @click="submit">
          {{ submitting ? '提交中…' : '保存课程' }}
        </button>
        <span v-if="message" :class="ok ? 'tip' : 'error'">{{ message }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { createCourse, listCourses, saveGoal } from '../api'

const courses = ref([])
const loading = ref(true)
const loadError = ref('')
const submitting = ref(false)
const saving = ref(false)
const message = ref('')
const ok = ref(true)
const goalMessage = ref('')
const goalOk = ref(true)
const form = reactive({ name: '', term: '' })
const goal = reactive({ course_id: 0, exam_date: '', target_score: 85, available_minutes: 120 })

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const data = await listCourses()
    courses.value = data.items
  } catch (e) {
    loadError.value = `课程加载失败：${e.message}`
  } finally {
    loading.value = false
  }
}

function pickGoal(course) {
  goal.course_id = course.course_id
  goal.exam_date = course.exam_date || ''
  goal.target_score = course.target_score || 85
  goal.available_minutes = course.available_minutes || 120
  goalMessage.value = ''
}

async function submitGoal() {
  saving.value = true
  goalMessage.value = ''
  try {
    await saveGoal(goal.course_id, {
      exam_date: goal.exam_date,
      target_score: goal.target_score,
      available_minutes: goal.available_minutes
    })
    goalOk.value = true
    goalMessage.value = '考试目标已保存'
    await load()
  } catch (e) {
    goalOk.value = false
    goalMessage.value = e.message
  } finally {
    saving.value = false
  }
}

async function submit() {
  submitting.value = true
  message.value = ''
  try {
    await createCourse({ name: form.name, term: form.term })
    ok.value = true
    message.value = '课程已保存'
    form.name = ''
    form.term = ''
    await load()
  } catch (e) {
    ok.value = false
    message.value = e.message
  } finally {
    submitting.value = false
  }
}

onMounted(load)
</script>
