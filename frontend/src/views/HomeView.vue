<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'

const router = useRouter()
const role = ref(localStorage.getItem('role') || '')
const jobs = ref([])
const tags = ref([])
const err = ref('')
const ok = ref('')
const form = ref({ lamp_tag_id: null, nominal_nm: 587.56, measured_nm: 587.5 })
let timer

const isWriter = computed(() => role.value === 'writer')
const selectedTag = computed(() => tags.value.find((t) => t.id === form.value.lamp_tag_id) || null)

async function refresh() {
  if (!localStorage.getItem('tok')) return
  try {
    const [j, t] = await Promise.all([api('/api/jobs'), api('/api/lamp-tags')])
    jobs.value = j
    tags.value = t
    if (!form.value.lamp_tag_id && t.length) {
      const firstOk = t.find((x) => x.active && x.calibratable)
      form.value.lamp_tag_id = firstOk ? firstOk.id : t[0].id
    }
  } catch (e) {
    err.value = String(e.message || e)
  }
}

async function submit() {
  err.value = ''
  ok.value = ''
  if (!form.value.lamp_tag_id) {
    err.value = '请先挂灯种牌（可到灯种牌页建档）'
    return
  }
  try {
    await api('/api/jobs', {
      method: 'POST',
      body: JSON.stringify({
        lamp_tag_id: form.value.lamp_tag_id,
        nominal_nm: form.value.nominal_nm,
        measured_nm: form.value.measured_nm,
      }),
    })
    ok.value = '已入队，等待处理'
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
}

function goDetail(id) {
  router.push(`/jobs/${id}`)
}

onMounted(() => {
  role.value = localStorage.getItem('role') || ''
  refresh()
  timer = setInterval(refresh, 1000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div>
    <p v-if="err" style="color:#b00020">{{ err }}</p>
    <p v-if="ok" style="color:#0a7d28">{{ ok }}</p>

    <section v-if="isWriter" style="margin:16px 0; padding:12px; border:1px solid #ccc;">
      <h3>提交校准（挂灯种牌入队）</h3>
      <p style="color:#666;font-size:13px;">入队必须绑灯种牌：仅「在牌且圈了可校准」的灯可提交，否则被拒收。称呼随单落盘冻结。</p>
      <label>
        灯种牌
        <select v-model.number="form.lamp_tag_id">
          <option v-for="t in tags" :key="t.id" :value="t.id">
            {{ t.name }}{{ !t.active ? '（已摘牌）' : !t.calibratable ? '（没圈可校准）' : '' }}
          </option>
        </select>
      </label>
      <label style="margin-left:12px;">标称 nm <input type="number" step="0.01" v-model.number="form.nominal_nm" /></label>
      <label style="margin-left:12px;">实测 nm <input type="number" step="0.01" v-model.number="form.measured_nm" /></label>
      <button style="margin-left:12px;" @click="submit">挂灯种牌入队</button>
      <p v-if="selectedTag && !(selectedTag.active && selectedTag.calibratable)" style="color:#a15c00;font-size:13px;">
        当前「{{ selectedTag.name }}」{{ !selectedTag.active ? '已摘牌' : '没圈可校准' }}，提交会被拒收。
      </p>
    </section>

    <table border="1" cellpadding="6" style="border-collapse:collapse; width:100%;">
      <thead>
        <tr>
          <th>编号</th><th>灯种（冻结称呼）</th><th>标称</th><th>实测</th><th>状态</th><th>结论</th><th>理由</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="j in jobs"
          :key="j.id"
          style="cursor:pointer"
          @click="goDetail(j.id)"
        >
          <td>{{ j.id }}</td>
          <td>{{ j.lamp }}</td>
          <td>{{ j.nominal_nm }}</td>
          <td>{{ j.measured_nm }}</td>
          <td>{{ j.status === 'pending' ? '待处理' : j.status === 'done' ? '已完成' : j.status }}</td>
          <td>{{ j.verdict }}</td>
          <td>{{ j.reason }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
