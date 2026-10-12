<template>
  <div>
    <div class="card">
      <h2>我的课程</h2>
      <div v-if="loading" class="tip">正在加载课程…</div>
      <div v-else-if="error" class="error">{{ error }}</div>
      <div v-else-if="courses.length === 0" class="empty">
        还没有课程，先创建一门课程。
      </div>
      <table v-else>
        <thead>
          <tr><th>课程</th><th>学期</th><th>创建时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="c in courses" :key="c.course_id">
            <td>{{ c.name }}</td>
            <td>{{ c.term || '—' }}</td>
            <td class="tip">{{ c.created_at }}</td>
          </tr>
        </tbody>
      </table>
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
import { createCourse, listCourses } from '../api'

const courses = ref([])
const loading = ref(true)
const error = ref('')
const submitting = ref(false)
const message = ref('')
const ok = ref(true)
const form = reactive({ name: '', term: '' })

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await listCourses()
    courses.value = data.items
  } catch (e) {
    error.value = `课程加载失败：${e.message}`
  } finally {
    loading.value = false
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
