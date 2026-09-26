<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api.js'

const role = ref(localStorage.getItem('role') || '')
const writer = computed(() => role.value === 'writer')
const active = ref([])
const retired = ref([])
const err = ref('')
const form = ref({ name: '', calibratable: true })

async function refresh() {
  if (!localStorage.getItem('tok')) return
  try {
    const data = await api('/api/lamps')
    active.value = data.active
    retired.value = data.retired
    err.value = ''
  } catch (e) {
    err.value = String(e.message || e)
  }
}

async function createLamp() {
  err.value = ''
  try {
    await api('/api/lamps', {
      method: 'POST',
      body: JSON.stringify({ name: form.value.name.trim(), calibratable: form.value.calibratable }),
    })
    form.value.name = ''
    form.value.calibratable = true
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
}

async function toggleCalibratable(lamp) {
  err.value = ''
  try {
    await api(`/api/lamps/${lamp.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ calibratable: !lamp.calibratable }),
    })
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
}

async function retire(lamp) {
  err.value = ''
  if (!window.confirm(`确定摘掉「${lamp.name}」？摘牌后不可复挂，旧单冻结称呼不变。`)) return
  try {
    await api(`/api/lamps/${lamp.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ active: false }),
    })
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
}

function fmt(t) {
  return t ? new Date(t).toLocaleString() : ''
}

onMounted(() => {
  role.value = localStorage.getItem('role') || ''
  refresh()
})
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }}</p>

    <section class="panel">
      <h3>上格 · 挂牌灯种维护</h3>
      <p class="hint">入队只能圈上格里仍「可校准」的灯；固定称呼建档后不可改。</p>

      <form v-if="writer" class="create-row" @submit.prevent="createLamp">
        <label>固定称呼 <input v-model="form.name" placeholder="如：氦灯甲" /></label>
        <label class="check">
          <input type="checkbox" v-model="form.calibratable" /> 圈可校准
        </label>
        <button type="submit">建档挂牌</button>
      </form>
      <p v-else class="hint">巡检员只读：不能建档、圈选或摘牌。</p>

      <table border="1" cellpadding="6" class="grid">
        <thead>
          <tr><th>编号</th><th>固定称呼</th><th>可校准</th><th>建档人</th><th>建档时间</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="l in active" :key="l.id">
            <td>{{ l.id }}</td>
            <td>{{ l.name }}</td>
            <td>
              <input
                v-if="writer"
                type="checkbox"
                :checked="l.calibratable"
                @change="toggleCalibratable(l)"
              />
              <span v-else>{{ l.calibratable ? '是' : '否' }}</span>
            </td>
            <td>{{ l.created_by }}</td>
            <td>{{ fmt(l.created_at) }}</td>
            <td>
              <button v-if="writer" type="button" @click="retire(l)">摘牌</button>
              <span v-else>—</span>
            </td>
          </tr>
          <tr v-if="!active.length"><td colspan="6" class="empty">暂无挂牌灯种</td></tr>
        </tbody>
      </table>
    </section>

    <section class="panel">
      <h3>下格 · 摘牌履历</h3>
      <table border="1" cellpadding="6" class="grid">
        <thead>
          <tr><th>编号</th><th>固定称呼</th><th>建档人</th><th>摘牌人</th><th>摘牌时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="l in retired" :key="l.id">
            <td>{{ l.id }}</td>
            <td>{{ l.name }}</td>
            <td>{{ l.created_by }}</td>
            <td>{{ l.retired_by }}</td>
            <td>{{ fmt(l.retired_at) }}</td>
          </tr>
          <tr v-if="!retired.length"><td colspan="5" class="empty">暂无摘牌记录</td></tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<style scoped>
.panel {
  margin: 16px 0;
  padding: 12px;
  border: 1px solid #ccc;
}
.create-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 8px 0 12px;
}
.create-row .check {
  display: flex;
  align-items: center;
  gap: 4px;
}
.grid {
  border-collapse: collapse;
  width: 100%;
}
.empty {
  text-align: center;
  color: #888;
}
.hint {
  color: #666;
  font-size: 13px;
}
.err {
  color: #b00020;
}
</style>
