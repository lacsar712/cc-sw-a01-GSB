<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api.js'

const role = ref(localStorage.getItem('role') || '')
const tags = ref([])
const events = ref([])
const err = ref('')
const ok = ref('')
const form = ref({ name: '', calibratable: true })
let timer

const isWriter = computed(() => role.value === 'writer')

async function refresh() {
  if (!localStorage.getItem('tok')) return
  try {
    const [t, e] = await Promise.all([api('/api/lamp-tags'), api('/api/lamp-events')])
    tags.value = t
    events.value = e
  } catch (ex) {
    err.value = String(ex.message || ex)
  }
}

async function createTag() {
  err.value = ''
  ok.value = ''
  if (!form.value.name.trim()) {
    err.value = '称呼不能为空'
    return
  }
  try {
    await api('/api/lamp-tags', {
      method: 'POST',
      body: JSON.stringify({ name: form.value.name.trim(), calibratable: form.value.calibratable }),
    })
    form.value.name = ''
    ok.value = '已建档'
    await refresh()
  } catch (ex) {
    err.value = String(ex.message || ex)
  }
}

async function delist(tag) {
  err.value = ''
  ok.value = ''
  if (!window.confirm(`确定摘掉灯种牌「${tag.name}」？摘牌后不能再挂它提交，旧单称呼不变。`)) return
  try {
    await api(`/api/lamp-tags/${tag.id}/delist`, { method: 'POST' })
    ok.value = `灯种牌「${tag.name}」已摘牌`
    await refresh()
  } catch (ex) {
    err.value = String(ex.message || ex)
  }
}

function fmt(at) {
  return at ? new Date(at).toLocaleString() : ''
}
function actionText(a) {
  return a === 'delist' ? '摘牌' : a === 'create' ? '建档' : a
}

onMounted(() => {
  role.value = localStorage.getItem('role') || ''
  refresh()
  timer = setInterval(refresh, 1500)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div>
    <p v-if="err" style="color:#b00020">{{ err }}</p>
    <p v-if="ok" style="color:#0a7d28">{{ ok }}</p>

    <!-- 上格：建档 + 在牌灯种 -->
    <section style="margin:16px 0; padding:12px; border:1px solid #ccc;">
      <h3>灯种牌建档（上格）</h3>
      <p class="hint" style="color:#666;font-size:13px;">
        建档时写下固定称呼并圈「可校准」。称呼随单落盘后冻结，事后摘牌改不了旧单上的称呼。
      </p>
      <template v-if="isWriter">
        <label>称呼 <input v-model="form.name" placeholder="如 氦灯甲" /></label>
        <label style="margin-left:12px;">
          <input type="checkbox" v-model="form.calibratable" /> 圈可校准
        </label>
        <button style="margin-left:12px;" @click="createTag">建档</button>
      </template>
      <p v-else class="hint" style="color:#a15c00;">巡检员只读：可查看灯种牌与冻结称呼，不能建档 / 摘牌 / 提交。</p>

      <table border="1" cellpadding="6" style="border-collapse:collapse; width:100%; margin-top:10px;">
        <thead>
          <tr><th>编号</th><th>称呼（固定）</th><th>可校准</th><th>状态</th><th>建档人</th>
            <th v-if="isWriter">操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="t in tags" :key="t.id" :style="{ opacity: t.active ? 1 : 0.55 }">
            <td>{{ t.id }}</td>
            <td>{{ t.name }}</td>
            <td>{{ t.calibratable ? '已圈' : '没圈' }}</td>
            <td>{{ t.active ? '在牌' : '已摘牌' }}</td>
            <td>{{ t.created_by }}</td>
            <td v-if="isWriter">
              <button type="button" :disabled="!t.active" @click="delist(t)">摘牌</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 下格：摘牌履历 -->
    <section style="margin:16px 0; padding:12px; border:1px solid #ccc;">
      <h3>摘牌履历（下格）</h3>
      <table border="1" cellpadding="6" style="border-collapse:collapse; width:100%;">
        <thead>
          <tr><th>#</th><th>灯种牌</th><th>动作</th><th>当时可校准</th><th>操作人</th><th>时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="e in events" :key="e.id">
            <td>{{ e.id }}</td>
            <td>{{ e.lamp_name }}</td>
            <td>{{ actionText(e.action) }}</td>
            <td>{{ e.calibratable ? '已圈' : '没圈' }}</td>
            <td>{{ e.actor }}</td>
            <td>{{ fmt(e.at) }}</td>
          </tr>
          <tr v-if="!events.length"><td colspan="6" style="color:#888;">暂无履历</td></tr>
        </tbody>
      </table>
    </section>
  </div>
</template>
