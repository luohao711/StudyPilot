<template>
  <div>
    <div class="card">
      <h2>资料问答</h2>
      <p class="tip" v-if="kb.sources && kb.sources.length">
        知识库：{{ kb.sources.length }} 篇资料 / {{ kb.total_chunks }} 个片段，片段 {{ kb.chunk_size }} 字、重叠
        {{ kb.chunk_overlap }} 字；检索方式：{{ kb.retrieval }}；模型：{{ kb.model }}
      </p>
      <div class="field">
        <textarea
          v-model="question"
          rows="2"
          maxlength="200"
          placeholder="例如：模型服务不可用时，学习计划的生成会怎么处理？"
        ></textarea>
      </div>
      <div class="field">
        <label>回答方式</label>
        <select v-model="mode">
          <option value="rag">RAG（先检索资料再回答）</option>
          <option value="direct">直接模型（不给资料）</option>
        </select>
        <label class="inline">Top-k</label>
        <input v-model.number="topK" type="number" min="1" max="8" class="short" />
        <button :disabled="asking || !question.trim()" @click="ask">
          {{ asking ? '查询中…' : '提问' }}
        </button>
      </div>
      <div v-if="error" class="error">{{ error }}</div>
    </div>

    <div v-if="result" class="card">
      <h2>回答</h2>
      <p class="answer">{{ result.answer }}</p>
      <p class="tip">
        <span :class="badgeClass">{{ badgeText }}</span>
        　模式 {{ result.mode === 'rag' ? 'RAG' : '直接模型' }}　Prompt {{ result.prompt_version }}　
        耗时 {{ result.latency_ms }} ms　tokens {{ totalTokens }}　费用约 {{ result.cost_yuan }} 元
      </p>
      <div v-if="result.citations.length" class="cites">
        <p><b>出处</b></p>
        <ul>
          <li v-for="check in citationChecks" :key="check.index">
            [{{ check.index }}] 《{{ check.source_title }}》 {{ check.section }}
            <span class="tip">（与回答词语重合度 {{ check.overlap }}，{{ check.reliable ? '对应' : '需人工复核' }}）</span>
          </li>
        </ul>
      </div>
      <p v-if="result.skipped_reason" class="tip">未调用模型：检索没有达到相关性阈值（{{ result.skipped_reason }}）。</p>
    </div>

    <div v-if="result && result.retrieved.length" class="card">
      <h2>检索片段（Top-{{ result.retrieved.length }}）</h2>
      <table>
        <thead>
          <tr><th>编号</th><th>分数</th><th>来源与章节</th><th>内容预览</th></tr>
        </thead>
        <tbody>
          <tr v-for="hit in result.retrieved" :key="hit.chunk_id">
            <td>[{{ hit.index }}]</td>
            <td>{{ hit.score }}</td>
            <td>《{{ hit.source_title }}》<br /><span class="tip">{{ hit.section }}</span></td>
            <td>{{ hit.preview }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { qaAsk, qaKb } from '../api'

const kb = ref({ sources: [], total_chunks: 0 })
const question = ref('')
const mode = ref('rag')
const topK = ref(3)
const asking = ref(false)
const error = ref('')
const result = ref(null)

const totalTokens = computed(() => (result.value?.usage?.total_tokens ?? 0))
const citationChecks = computed(() => (result.value?.citation_checks || []).filter((c) => c.index))
const badgeText = computed(() => {
  if (!result.value) return ''
  if (result.value.mode === 'direct') return '直接模型回答（未使用资料）'
  return result.value.sufficient ? '依据资料回答' : '资料不足/未依据资料'
})
const badgeClass = computed(() => (result.value?.mode === 'rag' && result.value?.sufficient ? 'badge done' : 'badge'))

async function ask() {
  asking.value = true
  error.value = ''
  try {
    result.value = await qaAsk({ question: question.value, mode: mode.value, top_k: topK.value })
  } catch (e) {
    error.value = `提问失败：${e.message}`
    result.value = null
  } finally {
    asking.value = false
  }
}

onMounted(async () => {
  try {
    kb.value = await qaKb()
  } catch (e) {
    error.value = `知识库信息加载失败：${e.message}`
  }
})
</script>
