const BASE = '/api'

async function request(url, options = {}) {
  const resp = await fetch(BASE + url, {
    headers: { 'Content-Type': 'application/json' },
    ...options
  })
  const data = await resp.json().catch(() => ({}))
  if (!resp.ok) {
    const err = new Error(data.message || `请求失败（HTTP ${resp.status}）`)
    err.code = data.code
    err.status = resp.status
    throw err
  }
  return data
}

const post = (url, body) => request(url, { method: 'POST', body: JSON.stringify(body) })
const put = (url, body) => request(url, { method: 'PUT', body: JSON.stringify(body) })

export const listCourses = () => request('/courses')
export const createCourse = (payload) => post('/courses', payload)

export const getGoal = (courseId) => request(`/courses/${courseId}/goal`)
export const saveGoal = (courseId, payload) => post(`/courses/${courseId}/goal`, payload)

export const listPoints = (courseId) => request(`/courses/${courseId}/points`)
export const addPoint = (courseId, payload) => post(`/courses/${courseId}/points`, payload)
export const updateMastery = (courseId, pointId, mastery) =>
  put(`/courses/${courseId}/points/${pointId}`, { mastery })

export const generatePlan = (payload) => post('/plans/generate', payload)
export const confirmPlan = (payload) => post('/plans/confirm', payload)
export const currentPlan = (courseId) => request(`/plans/current?course_id=${courseId}`)
export const adjustPlan = (courseId) => post('/plans/adjust', { course_id: courseId })

export const listTasks = (courseId, status = '') =>
  request(`/tasks?course_id=${courseId}${status ? `&status=${status}` : ''}`)
export const taskSuggestion = (taskId) => request(`/tasks/${taskId}/suggestion`)
export const completeTask = (taskId, payload) => post(`/tasks/${taskId}/complete`, payload)

export const qaKb = () => request('/qa/kb')
export const qaAsk = (payload) => post('/qa/ask', payload)
export const qaChunks = (sourceId, limit = 3) =>
  request(`/qa/kb/chunks?source_id=${sourceId}&limit=${limit}`)
