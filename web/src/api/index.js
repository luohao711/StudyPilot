const BASE = '/api'

async function request(url, options = {}) {
  const resp = await fetch(BASE + url, {
    headers: { 'Content-Type': 'application/json' },
    ...options
  })
  const data = await resp.json().catch(() => ({}))
  if (!resp.ok) {
    throw new Error(data.message || `请求失败（HTTP ${resp.status}）`)
  }
  return data
}

export const listCourses = () => request('/courses')
export const createCourse = (payload) =>
  request('/courses', { method: 'POST', body: JSON.stringify(payload) })
